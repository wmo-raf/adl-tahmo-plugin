import logging
from datetime import timedelta

from adl.core.models import ObservationRecord
from adl.core.registries import Plugin
from django.utils import timezone as dj_timezone

logger = logging.getLogger(__name__)


class TahmoPlugin(Plugin):
    type = "adl_tahmo_plugin"
    label = "ADL TAHMO Plugin"
    
    def get_urls(self):
        return []
    
    def get_data(self):
        network_conn_name = self.network_connection.name
        
        logger.info(f"[TAHMO_PLUGIN] Starting data processing for {network_conn_name}")
        
        station_links = self.network_connection.station_links.all()
        
        logger.debug(f"[TAHMO_PLUGIN] Found {len(station_links)} station links for {network_conn_name}")
        
        self.client = self.network_connection.get_api_client()
        
        stations_records_count = {}
        
        try:
            for station_link in station_links:
                station_name = station_link.station.name
                
                if not station_link.enabled:
                    logger.warning(f"[TAHMO_PLUGIN] Station link {station_name} is disabled.")
                    continue
                
                logger.debug(f"[TAHMO_PLUGIN] Processing data for {station_name}")
                
                station_link_records_count = self.process_station_link(station_link)
                
                stations_records_count[station_link.station.id] = station_link_records_count
        except Exception as e:
            logger.error(f"[TAHMO_PLUGIN] Error processing data for {network_conn_name}. {e}")
        
        return stations_records_count
    
    def process_station_link(self, station_link):
        station_name = station_link.station.name
        
        logger.debug(f"[TAHMO_PLUGIN] Getting latest data for {station_name}")
        
        station_variable_mappings = station_link.variable_mappings.all()
        
        if not station_variable_mappings:
            logger.warning(f"[TAHMO_PLUGIN] No variable mappings found for {station_name}.")
            return 0
        
        station_timezone = station_link.timezone
        
        # get the end date(current time now) in the station timezone
        end_date = dj_timezone.localtime(timezone=station_timezone)
        # set the end date to the start of the next hour
        end_date = end_date.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        
        if station_link.start_date:
            start_date = dj_timezone.localtime(station_link.start_date, station_timezone)
        else:
            # set to end_date of the previous hour
            start_date = end_date - timedelta(hours=1)
        
        start_date_utc_format = start_date.strftime("%Y-%m-%dT%H:%M:%SZ")
        end_date_utc_format = end_date.strftime("%Y-%m-%dT%H:%M:%SZ")
        
        # Get the latest data from the TAML API
        records = self.client.get_measurements(station_link.tahmo_station_code, start_date=start_date_utc_format,
                                               end_date=end_date_utc_format)
        
        observation_records = []
        
        for record in records:
            datetime = record.get("datetime")
            
            if not datetime:
                logger.debug(f"[TAHMO_PLUGIN] No datetime found in record {record}")
                return 0
            
            for variable_mapping in station_link.variable_mappings.all():
                adl_parameter = variable_mapping.adl_parameter
                tahmo_variable_shortcode = variable_mapping.tahmo_variable_shortcode
                tahmo_parameter_unit = variable_mapping.tahmo_parameter_unit
                
                value = record.get(tahmo_variable_shortcode)
                
                if value is None:
                    logger.debug(f"[TAHMO_PLUGIN] No data record found for parameter {adl_parameter.name}")
                    continue
                
                if adl_parameter.unit != tahmo_parameter_unit:
                    value = adl_parameter.convert_value_from_units(value, tahmo_parameter_unit)
                
                record_data = {
                    "station": station_link.station,
                    "parameter": adl_parameter,
                    "time": datetime,
                    "value": value,
                    "connection": station_link.network_connection,
                }
                
                param_obs_record = ObservationRecord(**record_data)
                observation_records.append(param_obs_record)
        
        records_count = len(observation_records)
        
        if observation_records:
            logger.debug(f"[TAHMO_PLUGIN] Saving {records_count} records for {station_name}.")
            ObservationRecord.objects.bulk_create(
                observation_records,
                update_conflicts=True,
                update_fields=["value"],
                unique_fields=["station", "parameter", "time", "connection"]
            )
        
        return records_count

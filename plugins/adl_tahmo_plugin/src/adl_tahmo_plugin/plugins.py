import logging
from datetime import timedelta

from adl.core.registries import Plugin

logger = logging.getLogger(__name__)


class TahmoPlugin(Plugin):
    type = "adl_tahmo_plugin"
    label = "ADL TAHMO Plugin"
    
    def get_default_start_date(self, station_link):
        end_date = self.get_default_end_date(station_link)
        # set to end_date of the previous hour
        start_date = end_date - timedelta(days=1)
        return start_date
    
    def get_start_date_from_db(self, station_link):
        start_date = super().get_start_date_from_db(station_link)
        
        if start_date:
            # add 1 minute to ensure we don't fetch already existing data
            start_date += timedelta(minutes=1)
        
        return start_date
    
    def get_station_data(self, station_link, start_date=None, end_date=None):
        start_date_utc_format = start_date.strftime("%Y-%m-%dT%H:%M:%SZ")
        end_date_utc_format = end_date.strftime("%Y-%m-%dT%H:%M:%SZ")
        
        tahmo_http_client = station_link.network_connection.get_api_client()
        
        records = tahmo_http_client.get_measurements(
            station_link.tahmo_station_code,
            start_date=start_date_utc_format,
            end_date=end_date_utc_format
        )
        
        return records

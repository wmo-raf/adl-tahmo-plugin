import requests
from django.core.cache import cache
from requests.auth import HTTPBasicAuth


# API Reference: https://tahmo.org/docs/TAHMO_Measurements_API_documentation_latest.pdf
class TahmoAPIClient:
    def __init__(self, api_key, api_secret, base_url='https://datahub.tahmo.org', use_cache=True):
        self.api_key = api_key
        
        if not base_url.endswith('/'):
            base_url += '/'
        
        self.base_url = base_url
        self.use_cache = use_cache
        
        self.auth = HTTPBasicAuth(api_key, api_secret)
    
    def get_stations(self):
        cache_key = f"{self.api_key}-tahmo-stations"
        if self.use_cache and cache.get(cache_key):
            return cache.get(cache_key)
        else:
            url = f'{self.base_url}services/assets/v2/stations'
            response = requests.get(url, auth=self.auth)
            
            response.raise_for_status()
            
            stations_data = response.json().get('data', [])
            
            stations_data_dict_by_code = {}
            for station in stations_data:
                station_code = str(station['code'])
                stations_data_dict_by_code[station_code] = station
            
            if self.use_cache:
                # cache for 24 hours
                cache.set(cache_key, stations_data_dict_by_code, 86400)
            
            return stations_data_dict_by_code
    
    def get_variables(self):
        cache_key = f"{self.api_key}-tahmo-variables"
        if self.use_cache and cache.get(cache_key):
            return cache.get(cache_key)
        
        url = f'{self.base_url}services/assets/v2/variables'
        response = requests.get(url, auth=self.auth)
        response.raise_for_status()
        
        variables = response.json().get('data', [])
        variables_dict_by_shortcode = {}
        
        for variable_item in variables:
            variable = variable_item.get("variable")
            variable_shortcode = variable.get("shortcode")
            if variable_shortcode:
                variables_dict_by_shortcode[variable_shortcode] = variable
        
        if self.use_cache:
            # cache for 24 hours
            cache.set(cache_key, variables_dict_by_shortcode, 86400)
        
        return variables_dict_by_shortcode
    
    def get_measurements(self, station_code, collection_type="raw", start_date=None, end_date=None, variable=None,
                         sensor=None):
        url = f'{self.base_url}services/measurements/v2/stations/{station_code}/measurements/{collection_type}'
        
        params = {
        }
        
        if start_date:
            params['start'] = start_date
        if end_date:
            params['end'] = end_date
        if variable:
            params['variable'] = variable
        if sensor:
            params['sensor'] = sensor
        
        response = requests.get(url, auth=self.auth, params=params)
        response.raise_for_status()
        
        results = response.json().get('results', [])
        data = None
        if results:
            series = results[0].get('series', [])
            if series:
                data = series[0]
        
        measurements_by_date = {}
        
        if data:
            columns = data.get('columns', [])
            values = data.get('values', [])
            
            for item in values:
                data = {col: val for col, val in zip(columns, item)}
                time = data.get('time')
                variable = data.get('variable')
                value = data.get('value')
                
                # Convert relative humidity from decimal to percentage
                if variable == "rh" and value is not None:
                    value = value * 100
                
                quality = data.get('quality', None)
                if not measurements_by_date.get(time):
                    measurements_by_date[time] = {"observation_time": time}
                if value and quality == 1:
                    measurements_by_date[time][variable] = value
        
        return list(measurements_by_date.values())

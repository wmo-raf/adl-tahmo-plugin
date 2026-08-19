import requests
from django.core.cache import cache
from requests.auth import HTTPBasicAuth
from dateutil import parser as date_parser

DEFAULT_BASE_URL = 'https://datahub.tahmo.org'
DEFAULT_TIMEOUT = 30

STATIONS_PATH = 'services/assets/v2/stations'
VARIABLES_PATH = 'services/assets/v2/variables'

# The ingestion diagnostic's shared HTTP status table. The category strings are
# written out rather than imported from core: an import of core's vocabulary
# would break this plugin at import time on an older core, and core drops any
# value it does not recognise anyway.
#
# 400 and 422 decline because a malformed request is our bug, 429 because rate
# limiting is our polling schedule, and 3xx because a redirect says nothing
# about the source. Nothing here ever stamps UNKNOWN: declining leaves core's
# read-time classification free to do better later, and a stamp does not.
STATUS_CATEGORIES = {
    401: "AUTH_FAILED",
    403: "PERMISSION_DENIED",
    404: "PATH_NOT_FOUND",
}


def category_for_status(status_code):
    """The diagnostic failure category for an HTTP status, or None when the
    status carries no honest one."""
    if status_code in STATUS_CATEGORIES:
        return STATUS_CATEGORIES[status_code]
    if status_code is not None and 500 <= status_code < 600:
        return "PROTOCOL_ERROR"
    return None


def _raise_for_status(response):
    """``raise_for_status()``, tagging the raised error for the diagnostic.

    The exception is stamped in place rather than wrapped, so the original
    type still matches core's own exception table and the traceback survives.
    A code from the server is proof the server answered, which is what makes
    every category derived from one layer 5.
    """
    try:
        response.raise_for_status()
    except requests.HTTPError as e:
        category = category_for_status(e.response.status_code)
        if category:
            e.adl_category = category
            e.adl_layer = 5
        raise


def _parsed_list(response, key):
    """The list under ``key`` in a response body that really is one.

    A 2xx is not proof of an API response: ``requests`` follows redirects, so
    an expired session that lands on an HTML login page arrives here as a
    clean 200. Both that and a JSON body without the key raise ``ValueError``
    — requests' own ``JSONDecodeError`` is one too — so a caller has a single
    type to catch for "answered, but not with what we asked for".
    """
    payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get(key), list):
        raise ValueError(f"The response carried no '{key}' list.")
    return payload[key]


# API Reference: https://tahmo.org/docs/TAHMO_Measurements_API_documentation_latest.pdf
class TahmoAPIClient:
    def __init__(self, api_key, api_secret, base_url=DEFAULT_BASE_URL, timeout=DEFAULT_TIMEOUT, retries=None,
                 use_cache=True):
        self.api_key = api_key

        if not base_url.endswith('/'):
            base_url += '/'

        self.base_url = base_url
        self.timeout = timeout
        self.use_cache = use_cache

        self.auth = HTTPBasicAuth(api_key, api_secret)

        self.session = requests.Session()
        if retries is not None:
            # Mounted only when asked for. requests' default adapter already
            # retries nothing, so the ingestion path keeps its behaviour and
            # the on-demand diagnostic checks can say so explicitly.
            adapter = requests.adapters.HTTPAdapter(max_retries=retries)
            self.session.mount("https://", adapter)
            self.session.mount("http://", adapter)

    def get_stations(self):
        cache_key = f"{self.api_key}-tahmo-stations"
        if self.use_cache and cache.get(cache_key):
            return cache.get(cache_key)

        url = f'{self.base_url}{STATIONS_PATH}'
        response = self.session.get(url, auth=self.auth, timeout=self.timeout)

        _raise_for_status(response)

        stations_data = _parsed_list(response, 'data')

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

        url = f'{self.base_url}{VARIABLES_PATH}'
        response = self.session.get(url, auth=self.auth, timeout=self.timeout)
        _raise_for_status(response)

        variables = _parsed_list(response, 'data')
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
        """
        Fetch measurements for one station, returning ``(records, sources_count)``.

        The count is of the raw entries the response carried for the requested
        window, read before the collapse into per-timestamp records and before
        the quality filter below — a count taken after either would let a
        filter of ours read as the source having offered nothing. It leaves
        the client by return value because the station link it is reported on
        belongs to the plugin, not here.
        """
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

        response = self.session.get(url, auth=self.auth, params=params, timeout=self.timeout)
        _raise_for_status(response)

        results = _parsed_list(response, 'results')
        data = None
        if results:
            series = results[0].get('series', [])
            if series:
                data = series[0]

        measurements_by_date = {}
        values = []

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
                    time_obj = date_parser.isoparse(time)
                    measurements_by_date[time] = {"observation_time": time_obj}
                if value and quality == 1:
                    measurements_by_date[time][variable] = value

        return list(measurements_by_date.values()), len(values)

"""
Tests for the ingestion-diagnostic contracts: ``get_source_endpoint()``,
``check_source()``, ``check_station_source()``, the ``adl_sources_count``
duck-typed handover and the exception stamping in ``client.py``. See the
"Ingestion Diagnostic Contracts" page in the ADL developer guide.

All tests run without touching the database: model instances are built
unsaved and the HTTP layer is stubbed, so the seam under test is exactly the
contract core consumes. That is what ``SimpleTestCase`` buys here — Django
still calls ``setup_databases()`` whatever the class, so the suite is run on
this plugin's own compose stack with ``make test`` from the repo root.
"""

import ast
import os
from datetime import datetime, timezone
from unittest import mock
from zoneinfo import ZoneInfo

import requests
from adl.core.source_checks import SourceCheckResult, SourceCheckStatus
from django.test import SimpleTestCase

from adl_tahmo_plugin.client import TahmoAPIClient, category_for_status
from adl_tahmo_plugin.models import TahmoConnection, TahmoStationLink
from adl_tahmo_plugin.plugins import TahmoPlugin

NOT_JSON = object()


class FakeResponse:
    """A stubbed ``requests`` response: status code, and a body that either
    parses or does not."""

    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.payload = payload

    def json(self):
        if self.payload is NOT_JSON:
            # What an HTML login page reached through a redirect looks like
            # from here. requests' own JSONDecodeError is a ValueError too.
            raise requests.exceptions.JSONDecodeError("Expecting value", "<html>", 0)
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Error", response=self)


class FakeAPIClient:
    """A stubbed TAHMO client that answers the one call a check makes."""

    def __init__(self, stations=None, error=None, measurements=None):
        self.stations = stations if stations is not None else {}
        self.error = error
        self.measurements = measurements
        self.measurement_calls = []

    def get_stations(self):
        if self.error is not None:
            raise self.error
        return self.stations

    def get_measurements(self, station_code, **kwargs):
        self.measurement_calls.append({"station_code": station_code, **kwargs})
        if self.error is not None:
            raise self.error
        return self.measurements


def station_record(code="TA00001", name="Nairobi — Dagoretti Corner"):
    return {"code": code, "location": {"name": name}}


def make_connection(**kwargs):
    kwargs.setdefault("api_key", "key")
    kwargs.setdefault("api_secret", "secret")
    return TahmoConnection(**kwargs)


def make_station_link(connection=None, **kwargs):
    kwargs.setdefault("tahmo_station_code", "TA00001")
    link = TahmoStationLink(**kwargs)
    link.network_connection = connection or make_connection()
    return link


def stub_api_client(client):
    """Patch the client factory, capturing the arguments the check passed."""
    calls = []

    def factory(self, **kwargs):
        calls.append(kwargs)
        return client

    patcher = mock.patch.object(TahmoConnection, "get_api_client", autospec=True,
                                side_effect=factory)
    return patcher, calls


class GetApiClientTests(SimpleTestCase):
    """The factory's defaults are the ingestion path's behaviour, unchanged;
    only the on-demand checks ask for anything else."""

    def test_defaults_are_todays_ingestion_behaviour(self):
        client = make_connection().get_api_client()
        self.assertTrue(client.use_cache)
        self.assertEqual(client.timeout, 30)

    def test_checks_can_bound_and_bypass(self):
        client = make_connection().get_api_client(use_cache=False, timeout=5, retries=0)
        self.assertFalse(client.use_cache)
        self.assertEqual(client.timeout, 5)


class GetSourceEndpointTests(SimpleTestCase):

    def test_names_the_host_the_client_dials(self):
        self.assertEqual(make_connection().get_source_endpoint(),
                         ("datahub.tahmo.org", 443))


class CheckSourceTests(SimpleTestCase):

    def check(self, connection):
        result = connection.check_source()
        self.assertIsInstance(result, SourceCheckResult)
        self.assertIn(result.status, SourceCheckStatus.ALL)
        return result

    def run_check(self, client, connection=None):
        connection = connection or make_connection()
        patcher, calls = stub_api_client(client)
        with patcher:
            result = self.check(connection)
        return result, calls

    def test_a_parsed_station_list_is_ok(self):
        result, _calls = self.run_check(FakeAPIClient(stations={"TA00001": station_record()}))
        self.assertEqual(result.status, SourceCheckStatus.OK)
        self.assertIsNone(result.category)
        self.assertIn("datahub.tahmo.org", result.message)
        self.assertIn("1", result.message)

    def test_bypasses_the_cache_and_bounds_the_call(self):
        _result, calls = self.run_check(FakeAPIClient(stations={}))
        self.assertEqual(calls, [{"use_cache": False, "timeout": 5, "retries": 0}])

    def test_classifies_from_the_status_the_server_sent(self):
        for status, category in ((401, "AUTH_FAILED"), (403, "PERMISSION_DENIED"),
                                 (404, "PATH_NOT_FOUND"), (500, "PROTOCOL_ERROR"),
                                 (503, "PROTOCOL_ERROR")):
            with self.subTest(status=status):
                error = requests.HTTPError(response=FakeResponse(status))
                result, _calls = self.run_check(FakeAPIClient(error=error))
                self.assertEqual(result.status, SourceCheckStatus.FAILED)
                self.assertEqual(result.category, category)
                self.assertIn(str(status), result.message)
                self.assertIn("/services/assets/v2/stations", result.message)

    def test_declines_a_status_that_is_not_the_sources_fault(self):
        for status in (400, 422, 429):
            with self.subTest(status=status):
                error = requests.HTTPError(response=FakeResponse(status))
                result, _calls = self.run_check(FakeAPIClient(error=error))
                self.assertEqual(result.status, SourceCheckStatus.FAILED)
                self.assertIsNone(result.category)

    def test_a_login_page_200_is_not_ok(self):
        # The body parsed as JSON but carried no station list, or did not
        # parse at all — either way the source said nothing we can trust.
        for error in (ValueError("The response carried no 'data' list."),
                      requests.exceptions.JSONDecodeError("Expecting value", "<html>", 0)):
            with self.subTest(error=type(error).__name__):
                result, _calls = self.run_check(FakeAPIClient(error=error))
                self.assertEqual(result.status, SourceCheckStatus.FAILED)
                self.assertIsNone(result.category)
                self.assertIn("not a station list", result.message)

    def test_a_codeless_failure_declines_the_category(self):
        # Core stamps every return layer 5, so a layer-4 category here would
        # have the diagnostic contradict itself about which layer failed.
        for error in (requests.ConnectionError("connection refused"),
                      requests.exceptions.SSLError("bad handshake"),
                      requests.exceptions.ReadTimeout("timed out")):
            with self.subTest(error=type(error).__name__):
                result, _calls = self.run_check(FakeAPIClient(error=error))
                self.assertEqual(result.status, SourceCheckStatus.FAILED)
                self.assertIsNone(result.category)
                self.assertIn("could not be reached", result.message)

    def test_survives_the_core_normaliser(self):
        from adl.core.source_checks import normalise_source_check_result
        result, _calls = self.run_check(FakeAPIClient(stations={"TA00001": station_record()}))
        self.assertEqual(normalise_source_check_result(result).status, SourceCheckStatus.OK)

    def test_core_detects_the_override(self):
        from adl.core.source_checks import connection_implements_check_source
        self.assertTrue(connection_implements_check_source(make_connection()))


class CheckStationSourceTests(SimpleTestCase):

    def check(self, link):
        result = link.check_station_source()
        self.assertIsInstance(result, SourceCheckResult)
        self.assertIn(result.status, SourceCheckStatus.ALL)
        return result

    def run_check(self, client, link=None):
        link = link or make_station_link()
        patcher, calls = stub_api_client(client)
        with patcher:
            result = self.check(link)
        return result, calls

    def test_a_present_code_is_ok_with_the_upstream_label(self):
        client = FakeAPIClient(stations={"TA00001": station_record()})
        result, _calls = self.run_check(client)
        self.assertEqual(result.status, SourceCheckStatus.OK)
        self.assertIn("TA00001", result.message)
        self.assertIn("Nairobi — Dagoretti Corner", result.message)

    def test_a_present_code_without_a_label_still_reads_cleanly(self):
        client = FakeAPIClient(stations={"TA00001": {"code": "TA00001"}})
        result, _calls = self.run_check(client)
        self.assertEqual(result.status, SourceCheckStatus.OK)
        self.assertIn("TA00001", result.message)

    def test_an_absent_code_is_proven_not_found(self):
        client = FakeAPIClient(stations={"TA00002": station_record(code="TA00002")})
        result, _calls = self.run_check(client)
        self.assertEqual(result.status, SourceCheckStatus.FAILED)
        self.assertEqual(result.category, "PATH_NOT_FOUND")
        self.assertIn("TA00001", result.message)

    def test_bypasses_the_cache(self):
        # Harder here than at connection scope: a day-old list would report a
        # station added upstream yesterday as proven missing.
        _result, calls = self.run_check(FakeAPIClient(stations={"TA00001": station_record()}))
        self.assertEqual(calls, [{"use_cache": False, "timeout": 5, "retries": 0}])

    def test_a_failed_read_is_never_converted_into_ok(self):
        for error in (requests.ConnectionError("connection refused"),
                      requests.HTTPError(response=FakeResponse(500)),
                      ValueError("The response carried no 'data' list.")):
            with self.subTest(error=type(error).__name__):
                result, _calls = self.run_check(FakeAPIClient(error=error))
                self.assertEqual(result.status, SourceCheckStatus.FAILED)
                self.assertNotEqual(result.category, "PATH_NOT_FOUND")

    def test_core_detects_the_override(self):
        from adl.core.source_checks import station_link_implements_check_station_source
        self.assertTrue(station_link_implements_check_station_source(make_station_link()))


class IngestionWindowTests(SimpleTestCase):
    """Core hands the window over in the station's timezone; the API reads
    the bounds as UTC, so they have to be converted, not relabelled."""

    def collect(self, start, end):
        client = FakeAPIClient(measurements=([], 0))
        patcher, _calls = stub_api_client(client)
        with patcher:
            TahmoPlugin().get_station_data(make_station_link(), start, end)
        return client.measurement_calls[0]

    def test_a_local_window_is_sent_as_utc_instants(self):
        nairobi = ZoneInfo("Africa/Nairobi")
        call = self.collect(datetime(2026, 8, 1, 13, 0, tzinfo=nairobi),
                            datetime(2026, 8, 1, 14, 0, tzinfo=nairobi))
        self.assertEqual(call["start_date"], "2026-08-01T10:00:00Z")
        self.assertEqual(call["end_date"], "2026-08-01T11:00:00Z")

    def test_a_utc_window_is_unchanged(self):
        call = self.collect(datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc),
                            datetime(2026, 8, 1, 11, 0, tzinfo=timezone.utc))
        self.assertEqual(call["start_date"], "2026-08-01T10:00:00Z")
        self.assertEqual(call["end_date"], "2026-08-01T11:00:00Z")


class SourcesCountTests(SimpleTestCase):
    """The count is committed only from something the source told us, and
    only once it has told us."""

    START = datetime(2026, 8, 1, tzinfo=timezone.utc)
    END = datetime(2026, 8, 2, tzinfo=timezone.utc)

    def collect(self, link, client):
        patcher, _calls = stub_api_client(client)
        with patcher:
            return TahmoPlugin().get_station_data(link, self.START, self.END)

    def test_counts_the_entries_the_response_carried(self):
        link = make_station_link()
        records = self.collect(link, FakeAPIClient(measurements=([{"observation_time": self.START}], 7)))
        self.assertEqual(link.adl_sources_count, 7)
        self.assertEqual(len(records), 1)

    def test_an_empty_answer_is_zero_not_silence(self):
        link = make_station_link()
        self.collect(link, FakeAPIClient(measurements=([], 0)))
        self.assertEqual(link.adl_sources_count, 0)

    def test_a_failed_call_makes_no_claim_at_all(self):
        # None, never 0: a run that never got an answer must not accuse the
        # source of having offered nothing.
        link = make_station_link()
        link.adl_sources_count = None
        with self.assertRaises(requests.ConnectionError):
            self.collect(link, FakeAPIClient(error=requests.ConnectionError("refused")))
        self.assertIsNone(link.adl_sources_count)

    def test_the_count_is_taken_before_the_quality_filter(self):
        # Two raw rows, one of which our own quality filter drops. The source
        # offered two; a count of one would read as a partly-empty source.
        payload = {"results": [{"series": [{
            "columns": ["time", "variable", "value", "quality"],
            "values": [
                ["2026-08-01T00:00:00Z", "te", 21.5, 1],
                ["2026-08-01T01:00:00Z", "te", 22.0, 0],
            ],
        }]}]}
        client = TahmoAPIClient(api_key="key", api_secret="secret")
        with mock.patch.object(client.session, "get", return_value=FakeResponse(200, payload)):
            records, count = client.get_measurements("TA00001")
        self.assertEqual(count, 2)
        self.assertEqual(len(records), 2)
        self.assertNotIn("te", records[1])


class ExceptionStampingTests(SimpleTestCase):
    """A failed ingestion run carries the source's own verdict into the
    activity log, stamped in place so core's type table still applies."""

    def get_stations(self, response):
        client = TahmoAPIClient(api_key="key", api_secret="secret")
        with mock.patch.object(client.session, "get", return_value=response):
            return client.get_stations()

    def test_stamps_a_classified_status_at_layer_5(self):
        for status, category in ((401, "AUTH_FAILED"), (403, "PERMISSION_DENIED"),
                                 (404, "PATH_NOT_FOUND"), (502, "PROTOCOL_ERROR")):
            with self.subTest(status=status):
                with self.assertRaises(requests.HTTPError) as caught:
                    self.get_stations(FakeResponse(status))
                self.assertEqual(caught.exception.adl_category, category)
                self.assertEqual(caught.exception.adl_layer, 5)

    def test_leaves_a_declined_status_unstamped(self):
        # Declining keeps core's read-time tier free to classify the row
        # later; a stamp — UNKNOWN above all — would block it permanently.
        for status in (400, 422, 429):
            with self.subTest(status=status):
                with self.assertRaises(requests.HTTPError) as caught:
                    self.get_stations(FakeResponse(status))
                self.assertFalse(hasattr(caught.exception, "adl_category"))

    def test_core_reads_the_stamp(self):
        from adl.core.classification import classify_failure
        with self.assertRaises(requests.HTTPError) as caught:
            self.get_stations(FakeResponse(401))
        self.assertEqual(classify_failure(caught.exception), ("AUTH_FAILED", 5))

    def test_a_body_that_is_not_a_station_list_raises(self):
        for payload in (NOT_JSON, {"error": "unauthorized"}, []):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    self.get_stations(FakeResponse(200, payload))

    def test_the_status_table_declines_what_is_not_the_sources_fault(self):
        self.assertIsNone(category_for_status(302))
        self.assertIsNone(category_for_status(429))
        self.assertEqual(category_for_status(404), "PATH_NOT_FOUND")


class OlderCoreImportSafetyTests(SimpleTestCase):
    """The plugin must import cleanly on a core release that predates the
    source-check contracts, so nothing may import ``adl.core.source_checks``
    at module level.

    The contracts import it lazily instead, inside the method that needs it.
    Never wrap that import in ``try/except ImportError``: on an older core the
    method is never called, so the handler is unreachable, and it would turn a
    genuine import failure into a silent "this plugin does not support the
    check".
    """

    # Every module this plugin ships. Extend it as the plugin grows more.
    MODULES = ["models.py", "plugins.py", "client.py", "apps.py", "views.py",
               "utils.py", "validators.py", "widgets.py", "wagtail_hooks.py"]

    DENIED = "adl.core.source_checks"

    def test_no_module_level_import_of_source_checks(self):
        package_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for name in self.MODULES:
            path = os.path.join(package_dir, name)
            if not os.path.exists(path):
                continue  # a module this plugin does not (yet) ship
            with open(path) as f:
                tree = ast.parse(f.read())
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Import, ast.ImportFrom)):
                    continue
                if node.col_offset != 0:
                    continue  # indented imports are lazy, inside a function
                names = [a.name for a in node.names]
                module = getattr(node, "module", "") or ""
                self.assertNotIn(
                    self.DENIED, [module] + names,
                    f"{name} imports {self.DENIED} at module level")

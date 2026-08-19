from urllib.parse import urlparse

import requests
from adl.core.models import NetworkConnection, StationLink, DataParameter, Unit
from django.db import models
from django.urls import reverse
from django.utils.translation import gettext, gettext_lazy as _
from modelcluster.fields import ParentalKey
from wagtail.admin.panels import MultiFieldPanel, FieldPanel, InlinePanel
from wagtail.models import Orderable

from .client import (
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT,
    STATIONS_PATH,
    TahmoAPIClient,
    category_for_status,
)
from .validators import validate_start_date
from .widgets import (
    TahmoStationSelectWidget,
    TahmoVariableSelectWidget,
)

# What the diagnostic's on-demand checks pass instead of the ingestion
# defaults. Core bounds its whole probe — DNS, TCP and the source check
# together — by a 15-second wall clock and abandons rather than kills a
# worker that overruns it, so the check has to come back first with a real
# verdict. Deliberately not a model field: an operator who raised it to 300
# for a slow partner would silently re-break the probe.
SOURCE_CHECK_TIMEOUT_SECONDS = 5


class TahmoConnection(NetworkConnection):
    """
    Model representing a connection to a TAHMO API.
    """
    station_link_model_string_label = "adl_tahmo_plugin.TahmoStationLink"

    api_key = models.CharField(max_length=255, verbose_name="API Key")
    api_secret = models.CharField(max_length=255, verbose_name="API Secret")

    panels = NetworkConnection.panels + [
        MultiFieldPanel([
            FieldPanel("api_key"),
            FieldPanel("api_secret"),
        ], heading=_("TAHMO API Credentials")),
    ]

    class Meta:
        verbose_name = "TAHMO API Connection"
        verbose_name_plural = "TAHMO API Connections"

    def get_extra_model_admin_links(self):
        columns = [
            {
                "label": _("View Metadata"),
                "url": reverse("tahmo_metadata_for_connection", args=[self.id]),
                "icon_name": "list-ul",
                "kwargs": {"attrs": {"target": "_blank"}}
            }
        ]

        return columns

    @property
    def source_host(self):
        """The data host this connection dials, for operator-facing messages."""
        return urlparse(DEFAULT_BASE_URL).hostname

    def get_api_client(self, use_cache=True, timeout=DEFAULT_TIMEOUT, retries=None):
        """
        Returns the TAHMO API client instance.

        The defaults are the ingestion path's behaviour, unchanged. The
        diagnostic's on-demand checks pass a bounded, cache-bypassed client
        instead.
        """
        return TahmoAPIClient(api_key=self.api_key, api_secret=self.api_secret,
                              timeout=timeout, retries=retries, use_cache=use_cache)

    def get_source_endpoint(self):
        """
        The (host, port) core's generic DNS -> TCP probe dials (layer 4 of the
        ingestion diagnostic).

        No model field configures the host: the client's own default base URL
        is the literal string requests dials, which makes naming it exactly as
        truthful as reading a field would be.
        """
        parsed = urlparse(DEFAULT_BASE_URL)
        return parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)

    def check_source(self):
        """
        Ask whether the source accepts our credentials and offers data
        (layer 5 of the ingestion diagnostic). Read-only, on demand only.

        The station list is the cheapest read that proves both halves at once,
        and it is asked for with the cache bypassed: it is otherwise held for
        24 hours, and a cached copy would report OK while the source is down —
        the precise failure this check exists to catch.
        """
        # Imported lazily: this module does not exist on a core release
        # predating the source-check contracts, where this method is never
        # called and a module-level import would kill the whole plugin.
        from adl.core.source_checks import SourceCheckResult, SourceCheckStatus

        host = self.source_host

        try:
            # Client construction belongs inside the guarded region, so a
            # credential fault reads as a check failure rather than an
            # unhandled error.
            client = self.get_api_client(use_cache=False, timeout=SOURCE_CHECK_TIMEOUT_SECONDS, retries=0)
            stations = client.get_stations()
        except requests.HTTPError as e:
            return SourceCheckResult(
                status=SourceCheckStatus.FAILED,
                category=category_for_status(e.response.status_code),
                message=gettext("%(host)s returned HTTP %(code)s for %(path)s.") % {
                    "host": host,
                    "code": e.response.status_code,
                    "path": f"/{STATIONS_PATH}",
                },
            )
        except ValueError:
            # Ordered ahead of RequestException on purpose: requests' own
            # JSONDecodeError is both, and it belongs here. The source sent no
            # code to classify from, so the category is declined.
            return SourceCheckResult(
                status=SourceCheckStatus.FAILED,
                message=gettext("%(host)s answered, but the response was not a station list.") % {
                    "host": host,
                },
            )
        except requests.RequestException as e:
            return SourceCheckResult(
                status=SourceCheckStatus.FAILED,
                message=gettext("%(host)s could not be reached: %(error)s") % {
                    "host": host,
                    "error": e,
                },
            )

        return SourceCheckResult(
            status=SourceCheckStatus.OK,
            message=gettext("%(host)s accepted our credentials and returned %(count)s station(s).") % {
                "host": host,
                "count": len(stations),
            },
        )


class TahmoStationLink(StationLink):
    """
    Model representing a link to a TAHMO station.
    """
    tahmo_station_code = models.CharField(max_length=255, verbose_name="Tahmo Station")
    start_date = models.DateTimeField(
        blank=True,
        null=True,
        validators=[validate_start_date],
        verbose_name=_("Collection Start Date"),
        help_text=_(
            "Collection never starts before this date. On the first run it is "
            "the start of the backfill; afterwards, moving it forward past the "
            "latest saved record skips the gap. Leave empty to start from the "
            "last 24 hours."
        ),
    )

    panels = StationLink.panels + [
        FieldPanel("tahmo_station_code", widget=TahmoStationSelectWidget),
        FieldPanel("start_date"),
        InlinePanel("variable_mappings", label=_("Station Variable Mapping"), heading=_("Station Variable Mappings")),
    ]

    class Meta:
        verbose_name = "TAHMO Station Link"
        verbose_name_plural = "TAHMO Stations Link"

    def __str__(self):
        return f"{self.tahmo_station_code} - {self.station} - {self.station.wigos_id}"

    def get_variable_mappings(self):
        """
        Returns the variable mappings for this station link.
        """
        return self.variable_mappings.all()

    def get_first_collection_date(self):
        """
        Returns the first collection date for this station link.
        Returns None if no start date is set.
        """
        return self.start_date

    def check_station_source(self):
        """
        Ask whether this station's TAHMO code resolves at the source (layer 5
        of the ingestion diagnostic, station-scoped).

        TAHMO offers no single-station read short of the measurements call
        itself, so the check is a membership test over the station list. The
        cache is bypassed over the whole check rather than only its failure
        branch: a day-old list would report a station added upstream yesterday
        as missing, causing the very misconfiguration the check exists to
        detect.
        """
        from adl.core.source_checks import SourceCheckResult, SourceCheckStatus

        connection = self.network_connection
        host = connection.source_host

        try:
            client = connection.get_api_client(use_cache=False, timeout=SOURCE_CHECK_TIMEOUT_SECONDS, retries=0)
            stations = client.get_stations()
        except ValueError:
            return SourceCheckResult(
                status=SourceCheckStatus.FAILED,
                message=gettext("%(host)s answered, but the response was not a station list.") % {
                    "host": host,
                },
            )
        except requests.RequestException as e:
            return SourceCheckResult(
                status=SourceCheckStatus.FAILED,
                message=gettext("Could not read the station list from %(host)s: %(error)s") % {
                    "host": host,
                    "error": e,
                },
            )

        station = stations.get(self.tahmo_station_code)

        if station is None:
            # Absent from a list the source really returned is proof, not
            # suspicion: this station link can never ingest anything.
            return SourceCheckResult(
                status=SourceCheckStatus.FAILED,
                category="PATH_NOT_FOUND",
                message=gettext("Station %(code)s was not found in the source's station list.") % {
                    "code": self.tahmo_station_code,
                },
            )

        # The upstream's own label is what catches a valid-but-wrong code — a
        # real station belonging to a different site — which is the failure
        # that yields plausible wrong data rather than an outage.
        label = (station.get("location") or {}).get("name") or ""

        if label:
            message = gettext('Station %(code)s found upstream as "%(label)s".') % {
                "code": self.tahmo_station_code,
                "label": label,
            }
        else:
            message = gettext("Station %(code)s was found in the source's station list.") % {
                "code": self.tahmo_station_code,
            }

        return SourceCheckResult(status=SourceCheckStatus.OK, message=message)


class TahmoStationLinkVariableMapping(Orderable):
    station_link = ParentalKey(TahmoStationLink, on_delete=models.CASCADE, related_name="variable_mappings")
    adl_parameter = models.ForeignKey(DataParameter, on_delete=models.CASCADE, verbose_name=_("ADL Parameter"))
    tahmo_variable_shortcode = models.CharField(max_length=255, verbose_name="TAHMO Variable")
    tahmo_parameter_unit = models.ForeignKey(Unit, on_delete=models.CASCADE,
                                             verbose_name=_("TAHMO Parameter Unit"))

    panels = [
        FieldPanel("adl_parameter"),
        FieldPanel("tahmo_variable_shortcode", widget=TahmoVariableSelectWidget),
        FieldPanel("tahmo_parameter_unit"),
    ]

    @property
    def source_parameter_name(self):
        """
        Returns the shortcode of the TAHMO variable.
        """
        return self.tahmo_variable_shortcode

    @property
    def source_parameter_unit(self):
        """
        Returns the unit of the TAHMO variable.
        """
        return self.tahmo_parameter_unit

from adl.core.models import NetworkConnection, StationLink, DataParameter, Unit
from django.db import models
from django.utils.translation import gettext_lazy as _
from modelcluster.fields import ParentalKey
from timezone_field import TimeZoneField
from wagtail.admin.panels import MultiFieldPanel, FieldPanel, InlinePanel
from wagtail.models import Orderable

from .client import TahmoAPIClient
from .validators import validate_start_date
from .widgets import (
    TahmoStationSelectWidget,
    TahmoVariableSelectWidget,
)


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
        return []
    
    def get_api_client(self):
        """
        Returns the TAHMO API client instance.
        """
        return TahmoAPIClient(api_key=self.api_key, api_secret=self.api_secret)


class TahmoStationLink(StationLink):
    """
    Model representing a link to a TAHMO station.
    """
    tahmo_station_code = models.CharField(max_length=255, verbose_name="Tahmo Station")
    timezone = TimeZoneField(default='UTC', verbose_name=_("Station Timezone"),
                             help_text=_("Timezone used by the station for recording observations"))
    start_date = models.DateTimeField(blank=True, null=True, validators=[validate_start_date],
                                      verbose_name=_("Start Date"),
                                      help_text=_("Start date for data pulling. Select a past date to include the "
                                                  "historical data. Leave blank for collecting realtime data only"), )
    
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

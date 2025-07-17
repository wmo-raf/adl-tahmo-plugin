from django.urls import path
from wagtail import hooks

from .views import (
    get_tahmo_stations_for_connection,
    get_tahmo_variables_for_connection,
    get_metadata
)


@hooks.register('register_admin_urls')
def urlconf_tahmo_plugin():
    return [
        path("adl-tahmo-plugin/tahmo-conn-stations/", get_tahmo_stations_for_connection,
             name="tahmo_stations_for_connection"),
        path("adl-tahmo-plugin/tahmo-conn-variables/",
             get_tahmo_variables_for_connection,
             name="tahmo_variables_for_connection"),
        path("adl-tahmo-plugin/metadata/<int:connection_id>/", get_metadata, name="tahmo_metadata_for_connection"),
    ]

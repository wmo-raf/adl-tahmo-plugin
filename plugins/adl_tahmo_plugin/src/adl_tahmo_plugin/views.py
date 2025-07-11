from adl.core.utils import get_object_or_none
from django.http import JsonResponse
from django.utils.translation import gettext_lazy as _

from .models import TahmoConnection


def get_tahmo_stations_for_connection(request):
    network_connection_id = request.GET.get('connection_id')
    
    if not network_connection_id:
        response = {
            "error": _("Network connection ID is required.")
        }
        return JsonResponse(response, status=400)
    
    network_conn = get_object_or_none(TahmoConnection, pk=network_connection_id)
    if not network_conn:
        response = {
            "error": _("The selected connection is not a Tahmo API Connection")
        }
        
        return JsonResponse(response, status=400)
    
    client = network_conn.get_api_client()
    
    stations_dict = client.get_stations()
    
    stations_list = []
    
    for key, station in stations_dict.items():
        station_code = station.get("code")
        station_name = station.get("location", {}).get("name", "")
        station_label = f"{station_name} ({station_code})"
        stations_list.append({"label": station_label, "value": station_code})
    
    return JsonResponse(stations_list, safe=False)


def get_tahmo_variables_for_connection(request):
    network_connection_id = request.GET.get('connection_id')
    
    if not network_connection_id:
        response = {
            "error": _("Network connection ID is required.")
        }
        return JsonResponse(response, status=400)
    
    network_conn = get_object_or_none(TahmoConnection, pk=network_connection_id)
    if not network_conn:
        response = {
            "error": _("The selected connection is not a Tahmo API Connection")
        }
        
        return JsonResponse(response, status=400)
    
    client = network_conn.get_api_client()
    variables_dict = client.get_variables()
    
    if not variables_dict:
        response = {
            "error": _("No variables found for the selected connection.")
        }
        return JsonResponse(response, status=404)
    
    variables_list = [
        {
            "label": f"{variable['description']} - {variable['shortcode']} ({variable['units']})",
            "value": variable['shortcode']
        }
        for variable in variables_dict.values()
        if variable.get('shortcode')  # Ensure shortcode exists
    ]
    
    return JsonResponse(variables_list, safe=False)

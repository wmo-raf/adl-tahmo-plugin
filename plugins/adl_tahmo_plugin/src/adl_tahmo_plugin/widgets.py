from django.forms import Widget
from django.urls import reverse


class TahmoStationSelectWidget(Widget):
    template_name = 'adl_tahmo_plugin/widgets/tahmo_station_select_widget.html'
    
    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        
        context.update({
            'tahmo_stations_url': reverse("tahmo_stations_for_connection"),
        })
        
        return context


class TahmoVariableSelectWidget(Widget):
    template_name = 'adl_tahmo_plugin/widgets/tahmo_variable_select_widget.html'
    
    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        
        context.update({
            'tahmo_variables_url': reverse("tahmo_variables_for_connection"),
        })
        
        return context

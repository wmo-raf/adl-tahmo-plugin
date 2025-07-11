from adl.core.registries import plugin_registry
from django.apps import AppConfig


class PluginNameConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = "adl_tahmo_plugin"
    
    def ready(self):
        from .plugins import TahmoPlugin
        
        plugin_registry.register(TahmoPlugin())

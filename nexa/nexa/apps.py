from django.apps import AppConfig


class NexaConfig(AppConfig):
    name = "nexa"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from . import search, throttle  # noqa: F401  register signal receivers

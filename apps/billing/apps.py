from django.apps import AppConfig


class BillingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.billing"
    verbose_name = "Tariflar va to'lovlar"

    def ready(self):
        # Obuna signallari — tarif ko'tarilganda Telegram tabrigi.
        from . import signals  # noqa: F401

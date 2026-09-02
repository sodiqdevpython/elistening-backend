import logging
import os
import sys
import threading

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class CatalogConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.catalog"
    verbose_name = "Katalog"

    def ready(self):
        # Signals — post_save da AIJob yaratamiz.
        from . import signals  # noqa: F401

        # PROD (REDIS_URL bor → USE_CELERY): AI ishlari CELERY worker + beat
        # orqali bajariladi. Bu yerda THREAD ishga tushirmaymiz — aks holda
        # har gunicorn worker'da (×3) va har celery worker'da ham thread
        # ochilib, poyga va ortiqcha yuk bo'lardi.
        from django.conf import settings
        if getattr(settings, "USE_CELERY", False):
            return

        # DEV (SQLite, redis yo'q): thread DB'ni poll qilib bajaradi.
        # Django autoreload asosiy va reloader jarayonlarini ikki marta ochadi.
        # Worker faqat asosiy jarayonda ishga tushsin.
        if os.environ.get("RUN_MAIN") != "true" and os.environ.get("RUN_AI_WORKER") != "1":
            # `runserver` da `RUN_MAIN` autoreload'ni bildiradi. Boshqa
            # kontekstlarda (masalan test, migrate) worker kerak emas.
            return

        # Testlar davomida ishga tushmasin
        if any(a in sys.argv for a in ("test", "migrate", "makemigrations",
                                       "collectstatic", "shell", "createsuperuser")):
            return

        # `DISABLE_AI_WORKER=1` — o'chirib qo'yish
        if os.environ.get("DISABLE_AI_WORKER") == "1":
            return

        try:
            from .ai_worker import run_forever
            t = threading.Thread(
                target=run_forever, name="ai-worker", daemon=True,
            )
            t.start()
            logger.info("catalog: AI worker background thread started (dev)")
        except Exception:
            logger.exception("catalog: failed to start AI worker thread")

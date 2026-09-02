from django.apps import AppConfig


class CommonConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.common"
    verbose_name = "Umumiy"

    def ready(self):
        # SQLite (dev) uchun WAL rejimi — o'qish va yozish parallel bo'ladi,
        # "database is locked" xatolari deyarli yo'q bo'ladi. Postgres'da
        # (prod) bu kod ishlamaydi (vendor tekshiruvi). `busy_timeout` esa
        # settings OPTIONS'da (30s) berilgan.
        from django.db.backends.signals import connection_created
        connection_created.connect(_enable_sqlite_wal, dispatch_uid="sqlite_wal")


def _enable_sqlite_wal(sender, connection, **kwargs):
    if connection.vendor != "sqlite":
        return
    try:
        cur = connection.cursor()
        cur.execute("PRAGMA journal_mode=WAL;")
        cur.execute("PRAGMA synchronous=NORMAL;")
        cur.execute("PRAGMA busy_timeout=30000;")
    except Exception:  # pragma: no cover — pragma xatosi kritik emas
        pass

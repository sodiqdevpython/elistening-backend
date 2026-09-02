"""Server (production) sozlamalari."""
from .base import *  # noqa: F401,F403

DEBUG = False

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

# TLS sertifikat o'rnatilgach (Cloudflare yoki nginx) buni `True` qiling.
# Hozircha `False`, aks holda HTTP orqali sinash cheksiz redirectga tushadi.
SECURE_SSL_REDIRECT = env("SECURE_SSL_REDIRECT", default=False)  # noqa: F405

CORS_ALLOW_ALL_ORIGINS = False

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"verbose": {"format": "{levelname} {asctime} {name} {message}", "style": "{"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "verbose"}},
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {"apps": {"handlers": ["console"], "level": "INFO", "propagate": False}},
}

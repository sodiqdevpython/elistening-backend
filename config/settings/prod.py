"""Server (production) sozlamalari."""
from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403

DEBUG = False

# --- SECRET_KEY qat'iy tekshiruvi -----------------------------------------
# `SECRET_KEY` bir vaqtning o'zida JWT'ni ham imzolaydi (SimpleJWT default
# `SIGNING_KEY` — shu). Serverda u atigi 12 bayt edi va PyJWT har so'rovda
# ogohlantirardi:
#
#     InsecureKeyLengthWarning: The HMAC key is 12 bytes long, which is
#     below the minimum recommended length of 32 bytes for SHA256.
#
# Bu shunchaki "shovqin" emas: qisqa kalit bilan HS256 imzosini tanlab olish
# (brute-force) real xavf — kimdir istalgan foydalanuvchi nomidan token
# yasay oladi. Shu bois prod'da qisqa yoki namunaviy kalit bilan **umuman
# ishga tushmaymiz**.
#
# Yangi kalit yasash:
#     python -c "from django.core.management.utils import get_random_secret_key as g; print(g())"
# va uni `backend/.env` dagi `SECRET_KEY=` ga qo'ying.
#
# > Kalit almashsa hamma bir marta qaytadan kiradi (eski JWT'lar bekor
# > bo'ladi). Bazadagi ma'lumotga hech qanday ta'siri yo'q.
_MIN_SECRET_LEN = 50

if len(SECRET_KEY) < _MIN_SECRET_LEN or "insecure" in SECRET_KEY:  # noqa: F405
    raise ImproperlyConfigured(
        f"SECRET_KEY juda qisqa yoki namunaviy ({len(SECRET_KEY)} belgi). "  # noqa: F405
        f"Kamida {_MIN_SECRET_LEN} belgi bo'lishi kerak - u JWT'ni ham "
        "imzolaydi. Yangisini yasang: "
        'python -c "from django.core.management.utils import '
        'get_random_secret_key as g; print(g())"'
        " - va backend/.env dagi SECRET_KEY= ga yozing."
    )

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

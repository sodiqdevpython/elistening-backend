"""Lokal ishlab chiqish sozlamalari.

LAN'da ulashish uchun (masalan telefondan `http://<PC-IP>:5173` ga kirish):
- Vite `host: true` bilan 0.0.0.0 da tinglaydi (vite.config.ts).
- Django `runserver 0.0.0.0:8001` bilan ishga tushirilsin.
- CORS istalgan http(s) origin'ni qabul qiladi (regex `.*`).
- CSRF ham istalgan http(s) origin'ni ishonchli deb biladi — dev uchun xavfsiz.
"""
import socket

from .base import *  # noqa: F401,F403

DEBUG = True
ALLOWED_HOSTS = ["*"]


def _local_ipv4s() -> list[str]:
    """PC ning LAN IP manzillarini topish — CSRF/CORS ro'yxatiga qo'shish uchun."""
    ips: set[str] = {"127.0.0.1", "localhost"}
    try:
        for res in socket.getaddrinfo(socket.gethostname(), None):
            ip = res[4][0]
            if ":" not in ip:  # IPv4 only
                ips.add(ip)
    except OSError:
        pass
    # Yana bir usul: UDP socket "0.0.0.0:0" ni istalgan tashqi IP'ga ochib
    # o'zining ishlatgan interfeys IP'sini ko'rsatadi.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    return sorted(ips)


_LAN_IPS = _local_ipv4s()
_DEV_PORTS = (5173, 8001, 3000)

# --- CORS ---
# `ALLOW_ALL_ORIGINS = True` credentials bilan ishlamaydi (browser `*` header'ni
# credentials bilan rad etadi). Shu bois regex bilan har qanday origin'ni ochib
# beramiz, credentials'ni ham yoqamiz.
CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOWED_ORIGIN_REGEXES = [r"^https?://.*"]

# --- CSRF ---
# Django 4+ da POST so'rovlari faqat CSRF_TRUSTED_ORIGINS ichidagi origin'lardan
# qabul qilinadi. Wildcard IP'lar qo'llab-quvvatlanmaydi — shu bois LAN IP'larni
# aniq ro'yxatga qo'shamiz.
CSRF_TRUSTED_ORIGINS = [
    f"http://{host}:{port}"
    for host in _LAN_IPS
    for port in _DEV_PORTS
] + [
    "http://localhost:5173", "http://192.168.1.178:8000",
    "http://127.0.0.1:5173", "http://192.168.1.178:8000",
]

# Dev'da statik fayllar manifest'siz xizmat qilinadi (collectstatic shart emas).
STORAGES["staticfiles"] = {  # noqa: F405
    "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
}


# Ishga tushishida terminalga LAN URL'larini yozamiz — foydalanuvchiga qulay.
def _print_lan_urls() -> None:
    lan_only = [ip for ip in _LAN_IPS if ip not in ("127.0.0.1", "localhost")]
    if not lan_only:
        return
    urls = [f"http://{ip}:5173  (frontend)" for ip in lan_only]
    urls += [f"http://{ip}:8001  (backend)" for ip in lan_only]
    import sys
    sys.stderr.write(
        "\n\033[92m📡 LAN'da ulashish uchun:\033[0m\n  " + "\n  ".join(urls) + "\n\n"
    )


try:
    _print_lan_urls()
except Exception:
    pass

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}

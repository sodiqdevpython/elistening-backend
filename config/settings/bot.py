"""Bot jarayoni uchun yengil Django sozlamalari.

Bot faqat ORM ishlatadi (kod yaratish, profil o'qish, xabar navbati),
shuning uchun DRF, admin, staticfiles kabi ilovalar yuklanmaydi —
botning virtual muhiti kichik bo'lib qoladi.

Baza sozlamalari `base.py` dan, ya'ni backend bilan bitta bazadan olinadi.
"""
from .base import *  # noqa: F401,F403

DEBUG = False

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "apps.common",
    "apps.accounts",
    # Taklif sovg'asi (tarif berish) bot jarayonida ham hisoblanadi, shu bois
    # billing modellari yuklanishi SHART — aks holda `invite_rewards`
    # teskari bog'lanishi topilmaydi.
    "apps.billing",
    "apps.telegrambot",
]

MIDDLEWARE = []

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}

"""Django ishga tushganda Celery ilovasini ham yuklaymiz.

Celery o'rnatilmagan muhitlarda (masalan, botning yengil venv'i faqat
ORM uchun ishlatiladi) import jim o'tkazib yuboriladi — bunday
jarayonlar baribir vazifa bajarmaydi.
"""
try:
    from .celery import app as celery_app
except ModuleNotFoundError:  # pragma: no cover - celery yo'q muhit
    celery_app = None

__all__ = ("celery_app",)

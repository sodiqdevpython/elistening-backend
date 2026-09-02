"""Standalone AI worker — alohida process bo'lib ishlashi mumkin.

Ishga tushirish:
    python manage.py run_ai_worker

Bu buyruq cheksiz aylanadi: har 5 s'da AIJob navbatidan bittasini oladi va
bajaradi. Server crash bo'lsa `running` bo'lib qolgan yozuvlar avtomatik
qayta pending'ga aylanadi (ai_worker._reap_stale).

Django `runserver` bilan birga ham ishlaydi — apps.py ready() da alohida
thread ochiladi. Ammo prod / uzun ish uchun bu buyruqni alohida jarayonda
ishga tushirish tavsiya etiladi.
"""
from django.core.management.base import BaseCommand

from apps.catalog import ai_worker


class Command(BaseCommand):
    help = "AI job workerni cheksiz aylantiradi (Whisper + Haiku navbatlari)"

    def add_arguments(self, parser):
        parser.add_argument("--poll", type=float, default=5.0,
                            help="Navbat bo'sh bo'lganda kutish soniyalari")
        parser.add_argument("--once", action="store_true",
                            help="Faqat bitta job bajarib chiqadi (test uchun)")

    def handle(self, *args, **opts):
        if opts["once"]:
            worked = ai_worker.run_pending_once()
            self.stdout.write(self.style.SUCCESS(
                f"Bir job bajarildi: {worked}"))
            return
        ai_worker.run_forever(poll_sec=opts["poll"])

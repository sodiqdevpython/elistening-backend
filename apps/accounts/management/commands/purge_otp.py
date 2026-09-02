"""Eskirgan kirish kodlarini o'chiradi.

Kod atigi 1 daqiqa yashaydi, shu bois muddati o'tgan yoki ishlatilgan qator
hech qachon kerak bo'lmaydi. Odatda tozalash O'ZI bo'ladi (bot yangi kod
berganda va sayt kodni tekshirganda), bu komanda esa bir martalik/cron uchun:

    python manage.py purge_otp
"""
from django.core.management.base import BaseCommand

from apps.accounts.models import TelegramOTP


class Command(BaseCommand):
    help = "Muddati o'tgan va ishlatilgan Telegram kirish kodlarini o'chiradi"

    def handle(self, *args, **options):
        before = TelegramOTP.objects.count()
        deleted = TelegramOTP.purge_expired()
        self.stdout.write(self.style.SUCCESS(
            f"{deleted} ta kod o'chirildi ({before} -> {TelegramOTP.objects.count()})"
        ))

"""Telegram bot orqali yuboriladigan bildirishnomalar."""
from django.conf import settings
from django.db import models

from apps.common.models import TimeStampedModel


class BotMessage(TimeStampedModel):
    """Bot yuborishi kerak bo'lgan xabar navbati.

    Bot alohida jarayon bo'lgani uchun backend to'g'ridan-to'g'ri
    Telegram API ga murojaat qilmaydi — xabarni shu jadvalga yozadi,
    bot esa uni o'qib yuboradi.
    """

    class Kind(models.TextChoices):
        REMINDER = "reminder", "Eslatma"
        STREAK = "streak", "Streak"
        BROADCAST = "broadcast", "Ommaviy xabar"
        SYSTEM = "system", "Tizim"

    class Status(models.TextChoices):
        PENDING = "pending", "Kutilmoqda"
        SENT = "sent", "Yuborilgan"
        FAILED = "failed", "Xatolik"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="Foydalanuvchi", null=True, blank=True,
        on_delete=models.CASCADE, related_name="bot_messages",
    )
    telegram_id = models.BigIntegerField("Telegram chat ID", db_index=True)
    kind = models.CharField("Turi", max_length=12, choices=Kind.choices, default=Kind.SYSTEM)
    text = models.TextField("Matn")
    status = models.CharField("Holat", max_length=10, choices=Status.choices,
                              default=Status.PENDING, db_index=True)
    error = models.TextField("Xatolik", blank=True)
    sent_at = models.DateTimeField("Yuborilgan vaqt", null=True, blank=True)

    class Meta:
        verbose_name = "Bot xabari"
        verbose_name_plural = "Bot xabarlari"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.telegram_id}: {self.text[:40]}"

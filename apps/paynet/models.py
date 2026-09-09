"""Paynet to'lovlari — tranzaksiya jurnali va servis paroli.

Hamyonning O'ZI bu yerda EMAS: u `apps/billing/models.py::Wallet` da, chunki
Click to'lovlari ham o'sha balansga tushadi.

## Nega hamyon (balans), to'g'ridan-to'g'ri tarif emas

Paynet universal interfeysi to'lovga "qaysi tarif" degan maydon BERMAYDI —
faqat `amount` (tiyinda) keladi. Ustiga `universal_tech_doc` Таблица 4
`PerformTransaction` javobida **to'lovdan keyingi balansni** majburiy qilib
qo'ygan, `GetInformation` misoli ham `balance` qaytaradi. Ya'ni Paynet bizni
depozit (hamyon) tutuvchi provayder deb ko'radi.

Shu bois oqim ikki bosqichli:

    Paynet to'lovi  →  Wallet.balance_tiyin += amount
    Sayt/ilova      →  balansdan tarif sotib olinadi (billing.grant_plan)

Bu bekor qilishni ham to'g'ri qiladi: pul hali balansda bo'lsa qaytaramiz,
tarifga sarflangan bo'lsa 77-kod ("Недостаточно средств на счету клиента
для отмены платежа") — hujjatda aynan shu holat uchun ajratilgan kod.

Pul TIYINDA saqlanadi — Paynet butun interfeys bo'ylab tiyin ishlatadi.
"""
from django.conf import settings
from django.db import models

from apps.common.models import TimeStampedModel


class PaynetTransaction(TimeStampedModel):
    """Paynet'dan kelgan bitta to'lov.

    `transaction_id` UNIQUE — bu **idempotentlik kafolati**. Paynet javobni
    olmay qolsa (timeout, tarmoq) o'sha so'rovni qaytadan yuboradi; unique
    indeks tufayli pul ikki marta yozilmaydi va biz eski javobni aynan
    qaytaramiz.

    `providerTrnId` sifatida `pk` ishlatiladi — alohida hisoblagich
    yaratmaymiz, chunki `pk` allaqachon noyob va o'zgarmas.
    """

    class State(models.IntegerChoices):
        # Qiymatlar Paynet spetsifikatsiyasidan (4-bo'lim) — o'zgartirilmaydi.
        SUCCESS = 1, "Muvaffaqiyatli"
        CANCELLED = 2, "Bekor qilingan"
        NOT_FOUND = 3, "Topilmadi"

    transaction_id = models.BigIntegerField(
        "Paynet tranzaksiya ID", unique=True, db_index=True,
        help_text="Paynet tomonidan berilgan `transactionId`.",
    )
    service_id = models.IntegerField("Servis ID", db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="Foydalanuvchi",
        # PROTECT: to'lov yozuvi moliyaviy hujjat — foydalanuvchi o'chirilsa
        # ham sverka (`GetStatement`) uchun saqlanib qolishi shart.
        on_delete=models.PROTECT, related_name="paynet_transactions",
    )
    amount_tiyin = models.BigIntegerField("Summa (tiyin)")
    state = models.SmallIntegerField(
        "Holat", choices=State.choices, default=State.SUCCESS, db_index=True,
    )
    fields = models.JSONField("Paynet yuborgan maydonlar", default=dict, blank=True)
    performed_at = models.DateTimeField("O'tkazilgan", db_index=True)
    cancelled_at = models.DateTimeField("Bekor qilingan", null=True, blank=True)

    class Meta:
        verbose_name = "Paynet tranzaksiyasi"
        verbose_name_plural = "Paynet tranzaksiyalari"
        ordering = ["-performed_at"]
        indexes = [
            # `GetStatement` aynan shu ustunlar bo'yicha so'raydi (servis +
            # davr), kuniga bir marta lekin butun sutkalik hajm bilan.
            models.Index(fields=["service_id", "state", "performed_at"]),
        ]

    def __str__(self):
        return f"#{self.transaction_id} — {self.amount_uzs} so'm ({self.get_state_display()})"

    @property
    def amount_uzs(self) -> int:
        return self.amount_tiyin // 100

    @property
    def provider_trn_id(self) -> int:
        return self.pk


class PaynetCredential(TimeStampedModel):
    """Web-servis paroli — `ChangePassword` metodi yozadigan YAGONA qator.

    Odatda login/parol `.env` da turadi (`PAYNET_LOGIN` / `PAYNET_PASSWORD`).
    Ammo Приложение №2 ning izohiga ko'ra UZPAYNET `ChangePassword` mavjud
    bo'lsa **birinchi muvaffaqiyatli ulanishda parolni almashtirishi shart**.
    Yangi parolni `.env` ga yozib bo'lmaydi (jarayon qayta ishga tushishi
    kerak edi), shu bois u shu yerga tushadi va env'dagi qiymatni BEKOR
    QILADI.

    Parol OCHIQ saqlanmaydi — Django `make_password`/`check_password`. Basic
    auth uchun bizga faqat "to'g'rimi" degan javob kerak, parolning o'zi emas.
    """

    password_hash = models.CharField("Parol (hash)", max_length=256)

    class Meta:
        verbose_name = "Paynet paroli"
        verbose_name_plural = "Paynet paroli"

    def __str__(self):
        return f"Paynet paroli (o'zgartirilgan: {self.updated_at:%d.%m.%Y %H:%M})"

    def save(self, *args, **kwargs):
        self.pk = 1  # singleton
        super().save(*args, **kwargs)

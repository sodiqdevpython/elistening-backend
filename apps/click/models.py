"""Click to'lovlari — buyurtma (invoys) jurnali.

## Nega Click'da BUYURTMA bor, Paynet'da yo'q

Paynet to'lovda faqat summani yuboradi — qaysi tarif ekani noma'lum, shu bois
u yerda "niyat" (`Wallet.pending_plan`) ishlatiladi. Click'da esa BIZ
to'lovni boshlaymiz va `merchant_trans_id` ni O'ZIMIZ beramiz. Demak har
to'lov aniq bir buyurtmaga bog'langan: qaysi foydalanuvchi, qaysi tarif,
necha oyga, qancha summa. Taxmin qilishga o'rin yo'q.

## Holatlar (Click kutubxonasidagi `PaymentsStatus` bilan bir xil)

    INPUT      buyurtma yaratildi, foydalanuvchi hali to'lamadi
    WAITING    Click `Prepare` yubordi — to'lov jarayonda
    CONFIRMED  Click `Complete` yubordi, error=0 — pul keldi
    REJECTED   bekor qilindi yoki Click xato bilan yakunladi

`CONFIRMED` — YAKUNIY holat: undan keyin hech qanday so'rov balansni qayta
o'zgartira olmaydi (`views.py` shuni tekshiradi).
"""
from decimal import Decimal

from django.conf import settings
from django.db import models

from apps.common.models import TimeStampedModel


class ClickOrder(TimeStampedModel):
    """Bitta Click to'lovi. `pk` — Click'ga beriladigan `merchant_trans_id`.

    Alohida hisoblagich yaratmaymiz: `pk` allaqachon noyob va o'zgarmas,
    Click esa `merchant_trans_id` ni satr sifatida qaytaradi.
    """

    class Status(models.TextChoices):
        INPUT = "input", "Yaratildi"
        WAITING = "waiting", "To'lov jarayonda"
        CONFIRMED = "confirmed", "To'landi"
        REJECTED = "rejected", "Bekor qilindi"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="Foydalanuvchi",
        # PROTECT: to'lov yozuvi moliyaviy hujjat — foydalanuvchi o'chirilsa
        # ham Click bilan sverka uchun saqlanib qolishi shart.
        on_delete=models.PROTECT, related_name="click_orders",
    )
    plan = models.ForeignKey(
        "billing.Plan", verbose_name="Tarif", on_delete=models.PROTECT, related_name="click_orders",
    )
    months = models.PositiveSmallIntegerField("Necha oyga", default=1)
    # Click SO'MDA ishlaydi (kasr bilan: "23000.00"), hamyon esa TIYINDA.
    # Ikkalasini ham saqlaymiz: so'm — Click bilan solishtirish uchun,
    # tiyin — hamyonga yozish uchun. Chegarada bir marta aylantiriladi.
    amount_uzs = models.DecimalField("Summa (so'm)", max_digits=12, decimal_places=2)
    status = models.CharField("Holat", max_length=12, choices=Status.choices,
                              default=Status.INPUT, db_index=True)
    status_note = models.CharField("Izoh", max_length=200, blank=True)

    # ── Click bergan identifikatorlar ────────────────────────────────────
    click_trans_id = models.BigIntegerField("Click tranzaksiya ID", null=True, blank=True,
                                            db_index=True)
    click_paydoc_id = models.BigIntegerField("Click to'lov hujjati", null=True, blank=True)
    paid_at = models.DateTimeField("To'langan", null=True, blank=True)

    class Meta:
        verbose_name = "Click buyurtmasi"
        verbose_name_plural = "Click buyurtmalari"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "status"])]

    def __str__(self):
        return f"#{self.pk} — {self.plan} × {self.months} oy ({self.get_status_display()})"

    @property
    def amount_tiyin(self) -> int:
        # So'm → tiyin. `Decimal` ga majburan aylantiramiz: yangi yaratilgan
        # obyektda maydon hali `int` bo'lishi mumkin (Django uni faqat
        # bazadan o'qiganda Decimal qiladi). Yaxlitlash yo'q — so'mning
        # kasr qismi ko'paytirilgach doim butun tiyin beradi.
        return int(Decimal(self.amount_uzs) * 100)

    @property
    def is_final(self) -> bool:
        """To'langan buyurtmaga qayta tegilmaydi."""
        return self.status == self.Status.CONFIRMED

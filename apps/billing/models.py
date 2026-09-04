"""Tariflar va obunalar.

To'lov tizimi (Click) HOZIRCHA ULANMAGAN — faqat ma'lumot modeli va admin
CRUD mavjud. `Payment` modeli kelajakdagi integratsiya uchun seam sifatida
turadi; hech qanday tashqi so'rov yuborilmaydi.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.common.models import OrderedModel, TimeStampedModel


class Plan(OrderedModel):
    code = models.SlugField("Kod", max_length=32, unique=True)
    name_uz = models.CharField("Nomi (uz)", max_length=80)
    name_en = models.CharField("Nomi (en)", max_length=80)
    price_uzs = models.PositiveIntegerField("Narx (so'm/oy)", default=0)
    price_usd = models.DecimalField("Narx ($/oy)", max_digits=6, decimal_places=2, default=0)
    features_uz = models.JSONField("Imkoniyatlar (uz)", default=list, blank=True)
    features_en = models.JSONField("Imkoniyatlar (en)", default=list, blank=True)
    is_default = models.BooleanField("Standart (yangi foydalanuvchiga)", default=False)

    # Eski kvotalar (ishlatilmaydi — pastdagi tur-bo'yicha limitlar amal qiladi).
    daily_lesson_limit = models.PositiveIntegerField("Kunlik dars limiti (0 = cheksiz)", default=0)
    daily_exam_limit = models.PositiveIntegerField("Kunlik test limiti (0 = cheksiz)", default=0)

    # Kunlik limitlar (tur bo'yicha). NULL/bo'sh = CHEKSIZ, 0 = umuman mumkin emas,
    # musbat son = shuncha (noyob kontent) kuniga.
    daily_shorts_limit = models.IntegerField("Kunlik Shorts (bo'sh = cheksiz)", null=True, blank=True)
    daily_video_limit = models.IntegerField("Kunlik Video (bo'sh = cheksiz)", null=True, blank=True)
    daily_dictation_limit = models.IntegerField("Kunlik Diktant (bo'sh = cheksiz)", null=True, blank=True)
    daily_ielts_limit = models.IntegerField("Kunlik IELTS test (bo'sh = cheksiz)", null=True, blank=True)

    #: Tarif kodi → creative STATUS nomi. STATIC (bazadagi nomga bog'liq emas),
    #: tarjimasiz — frontend/mobil bilan bir xil. Admin ro'yxati/dropdown va
    #: str(plan) shu nomni ko'rsatadi.
    STATUS_NAMES = {"free": "Qaldirg'och", "plus": "Jo'shqin", "pro": "Bo'talog'im"}

    class Meta(OrderedModel.Meta):
        verbose_name = "Tarif"
        verbose_name_plural = "Tariflar"

    @property
    def status_name(self) -> str:
        return self.STATUS_NAMES.get(self.code, self.name_uz)

    def __str__(self):
        return self.status_name

    def limit_for(self, kind: str):
        """`kind` (shorts|video|dictation|ielts) uchun kunlik limit; None = cheksiz."""
        return getattr(self, f"daily_{kind}_limit", None)

    @property
    def price_label_uz(self) -> str:
        return "0 so'm" if not self.price_uzs else f"{self.price_uzs:,}".replace(",", " ") + " so'm"

    @property
    def price_label_en(self) -> str:
        return "$0" if not self.price_usd else f"${self.price_usd}"


class Reason(models.TextChoices):
    """Tarif QANDAY olingani. Profil tarixida va botdagi xabarda ko'rinadi."""

    PAID = "paid", "To'lov"
    INVITE = "invite", "Do'st taklif qilgani uchun"
    MANUAL = "manual", "Admin qo'lda bergan"
    TEST = "test", "Test akkaunt"
    FREE = "free", "Bepul tarif"


class Subscription(TimeStampedModel):
    """Foydalanuvchining JORIY tarifi — har foydalanuvchida bittadan.

    Tarix bu yerda EMAS: har o'zgarish `SubscriptionEvent` ga alohida qator
    bo'lib yoziladi. Shu bois "hozir qaysi tarif" savoli har doim bitta
    qatordan o'qiladi va limit hisobi chalkashmaydi.
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "Faol"
        EXPIRED = "expired", "Muddati tugagan"
        CANCELLED = "cancelled", "Bekor qilingan"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="Foydalanuvchi",
        on_delete=models.CASCADE, related_name="subscriptions",
    )
    plan = models.ForeignKey(Plan, verbose_name="Tarif", on_delete=models.PROTECT, related_name="subscriptions")
    status = models.CharField("Holat", max_length=12, choices=Status.choices, default=Status.ACTIVE)
    started_at = models.DateTimeField("Boshlangan", default=timezone.now)
    expires_at = models.DateTimeField("Tugaydi", null=True, blank=True)
    reason = models.CharField(
        "Sababi", max_length=12, choices=Reason.choices, default=Reason.MANUAL,
        help_text="Tarif qanday olingan: to'lov, taklif, admin yoki test.",
    )

    class Meta:
        verbose_name = "Obuna"
        verbose_name_plural = "Obunalar"
        ordering = ["-started_at"]
        # Har foydalanuvchida BITTA joriy obuna. Tarix `SubscriptionEvent` da.
        constraints = [
            models.UniqueConstraint(fields=["user"], name="uniq_current_subscription"),
        ]

    def __str__(self):
        return f"{self.user} — {self.plan}"

    @property
    def is_active(self) -> bool:
        if self.status != self.Status.ACTIVE:
            return False
        return self.expires_at is None or self.expires_at > timezone.now()


class SubscriptionEvent(TimeStampedModel):
    """Tarif tarixi — O'ZGARMAS yozuv (append-only).

    Foydalanuvchi profilida "qachon, qaysi tarifni, qanday yo'l bilan oldim va
    qachongacha amal qiladi" shu jadvaldan ko'rsatiladi; bot xabari ham shu
    yozuv asosida yuboriladi.

    Nega alohida jadval: `Subscription` yangilanadi (bitta qator), tarix esa
    yo'qolmasligi kerak. Bu yozuvlar hech qachon o'zgartirilmaydi/o'chirilmaydi.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="Foydalanuvchi",
        on_delete=models.CASCADE, related_name="subscription_events",
    )
    plan = models.ForeignKey(Plan, verbose_name="Tarif", on_delete=models.PROTECT,
                             related_name="events")
    reason = models.CharField("Sababi", max_length=12, choices=Reason.choices,
                              default=Reason.MANUAL)
    months = models.PositiveIntegerField("Necha oyga", default=0,
                                         help_text="0 = muddatsiz yoki noma'lum.")
    started_at = models.DateTimeField("Boshlangan", default=timezone.now)
    expires_at = models.DateTimeField("Tugaydi", null=True, blank=True)
    note = models.CharField("Izoh", max_length=200, blank=True)

    class Meta:
        verbose_name = "Tarif tarixi"
        verbose_name_plural = "Tarif tarixi"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "-created_at"])]

    def __str__(self):
        return f"{self.user} — {self.plan} ({self.get_reason_display()})"


class InviteReward(TimeStampedModel):
    """Taklif uchun berilgan sovg'a — sarflangan takliflar LEDGER'i.

    Bu jadval "kim nechta taklifni allaqachon sovg'aga aylantirgan" ni
    saqlaydi. Mavjud sovg'a: `invited_count - SUM(invites_spent)`.

    **Nega ledger:** agar shunchaki `invited_count >= 20` deb tekshirsak,
    bir marta 20 ta taklif qilgan odam HAR OY qayta sovg'a olaverardi (yoki
    kod ikki marta ishlasa ikki marta). Ledger bilan har taklif faqat BIR
    MARTA sarflanadi va qolgani keyingi sovg'a uchun to'planib boradi.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="Foydalanuvchi",
        on_delete=models.CASCADE, related_name="invite_rewards",
    )
    plan = models.ForeignKey(Plan, verbose_name="Berilgan tarif", on_delete=models.PROTECT,
                             related_name="invite_rewards")
    months = models.PositiveIntegerField("Necha oy", default=1)
    invites_spent = models.PositiveIntegerField("Sarflangan taklif")
    event = models.ForeignKey(
        SubscriptionEvent, verbose_name="Tarix yozuvi", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="invite_rewards",
    )

    class Meta:
        verbose_name = "Taklif sovg'asi"
        verbose_name_plural = "Taklif sovg'alari"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user} — {self.plan} × {self.months} oy ({self.invites_spent} taklif)"


class Payment(TimeStampedModel):
    """To'lov yozuvi. Provayder integratsiyasi keyinroq ulanadi."""

    class Provider(models.TextChoices):
        CLICK = "click", "Click"
        MANUAL = "manual", "Qo'lda"

    class Status(models.TextChoices):
        PENDING = "pending", "Kutilmoqda"
        PAID = "paid", "To'langan"
        FAILED = "failed", "Xatolik"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="Foydalanuvchi",
        on_delete=models.CASCADE, related_name="payments",
    )
    plan = models.ForeignKey(Plan, verbose_name="Tarif", on_delete=models.PROTECT, related_name="payments")
    provider = models.CharField("Provayder", max_length=12, choices=Provider.choices, default=Provider.MANUAL)
    status = models.CharField("Holat", max_length=12, choices=Status.choices, default=Status.PENDING)
    amount_uzs = models.PositiveIntegerField("Summa (so'm)", default=0)
    external_id = models.CharField("Tashqi ID", max_length=120, blank=True)
    raw = models.JSONField("Xom ma'lumot", default=dict, blank=True)

    class Meta:
        verbose_name = "To'lov"
        verbose_name_plural = "To'lovlar"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user} — {self.amount_uzs} ({self.status})"


class DailyUsage(TimeStampedModel):
    """Kunlik limit hisobi — har (foydalanuvchi, sana, tur, kontent) NOYOB.

    Bir kontent kuni ichida bir marta sanaladi (qayta ko'rish yana sanamaydi).
    Limit = shu (user, date, kind) bo'yicha qatorlar soni.
    """

    KINDS = ("shorts", "video", "dictation", "ielts")

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="daily_usage",
    )
    date = models.DateField("Sana", db_index=True)
    kind = models.CharField("Tur", max_length=16)
    ref = models.CharField("Kontent", max_length=64)

    class Meta:
        verbose_name = "Kunlik ishlatilish"
        verbose_name_plural = "Kunlik ishlatilish"
        unique_together = ("user", "date", "kind", "ref")
        indexes = [models.Index(fields=["user", "date", "kind"])]

    def __str__(self):
        return f"{self.user} · {self.date} · {self.kind}"

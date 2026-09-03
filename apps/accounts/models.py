"""Foydalanuvchi va kunlik faollik.

Streak (ketma-ket kunlar) tizimdan olib tashlandi — foydalanuvchi uni
so'ramaydi va u har kuni kirishga majburlaydigan mexanika edi.
"""
import secrets
import string

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone

from apps.common.models import CEFR, LEVEL_HOURS_REQUIRED, TimeStampedModel, next_cefr

ALPHABET = string.ascii_uppercase + string.digits


def stats_cache_key(user_id) -> str:
    """`GET /api/me/stats/` javobi shu kalitda keshlanadi.

    Statistika to'rtta OG'IR agregatdan iborat (jami / 7 kun / 30 kun / faol
    kunlar) va profil ekrani har fokuslanganda so'raydi. Kesh **yozuvda
    tozalanadi** (`touch_activity`), shu bois hech qachon eskirmaydi — TTL
    faqat zaxira.
    """
    return f"me_stats_v1_{user_id}"


def generate_invite_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(6))


class User(AbstractUser):
    """Telegram orqali kiradigan foydalanuvchi.

    `username` Django talab qilgani uchun qoldirildi — Telegram orqali
    kirganlarda u `tg<chat_id>` ko'rinishida avtomatik yaratiladi.
    """

    class Gender(models.TextChoices):
        MALE = "male", "Erkak"
        FEMALE = "female", "Ayol"
        UNKNOWN = "unknown", "Ko'rsatilmagan"

    telegram_id = models.BigIntegerField(
        "Telegram chat ID", unique=True, null=True, blank=True, db_index=True
    )
    telegram_username = models.CharField("Telegram username", max_length=64, blank=True)
    display_name = models.CharField("Ko'rinadigan ism", max_length=120, blank=True)
    avatar = models.ImageField("Avatar", upload_to="avatars/", null=True, blank=True)
    cefr_level = models.CharField(
        "CEFR daraja", max_length=2, choices=CEFR.choices, default=CEFR.A1, db_index=True
    )
    gender = models.CharField(
        "Jins", max_length=10, choices=Gender.choices, default=Gender.UNKNOWN
    )
    language = models.CharField(
        "Interfeys tili", max_length=2, choices=[("uz", "O'zbekcha"), ("en", "English")], default="uz"
    )

    last_active_at = models.DateTimeField("Oxirgi faollik", null=True, blank=True)

    invite_code = models.CharField(
        "Taklif kodi", max_length=12, unique=True, default=generate_invite_code, db_index=True
    )
    invited_by = models.ForeignKey(
        "self", verbose_name="Kim taklif qilgan", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="invitees",
    )

    class Meta:
        verbose_name = "Foydalanuvchi"
        verbose_name_plural = "Foydalanuvchilar"
        ordering = ["-date_joined"]

    def __str__(self):
        return self.display_name or self.username

    def save(self, *args, **kwargs):
        if not self.invite_code:
            self.invite_code = generate_invite_code()
        super().save(*args, **kwargs)

    # --- Hisoblanadigan qiymatlar ----------------------------------------
    @property
    def initial(self) -> str:
        source = self.display_name or self.username or "?"
        return source.strip()[:1].upper()

    @property
    def invited_count(self) -> int:
        """JAMI hisoblangan taklif — sovg'aga sarflangani ham shu yerda qoladi.

        `Invitation` qatorlari sanaladi (`invitees` emas): faqat CHINDAN yangi
        va tekshiruvdan o'tgan foydalanuvchilar shu jadvalga tushadi.
        """
        return self.sent_invites.count()

    @property
    def invites_to_next_reward(self) -> int:
        """Keyingi sovg'agacha yana nechta taklif kerak.

        Sovg'a JAMI taklif soniga bog'langan (har 20 tada bittasi), shu bois
        bu "qoldiq balans" emas, oddiy `20 - (jami % 20)`.
        Batafsil: `apps/billing/rewards.py`.
        """
        from apps.billing.rewards import next_reward

        return next_reward(self.invited_count)[2]

    @property
    def next_level(self) -> str:
        return next_cefr(self.cefr_level)

    @property
    def required_hours(self) -> int:
        return LEVEL_HOURS_REQUIRED.get(self.cefr_level, 40)

    def seconds_in_period(self, days: int) -> int:
        since = timezone.localdate() - timezone.timedelta(days=days - 1)
        agg = self.daily_activity.filter(date__gte=since).aggregate(models.Sum("seconds"))
        return agg["seconds__sum"] or 0

    @property
    def total_seconds(self) -> int:
        agg = self.daily_activity.aggregate(models.Sum("seconds"))
        return agg["seconds__sum"] or 0

    def touch_activity(self, seconds: int = 0):
        """Kunlik faollikka soniya qo'shadi va oxirgi faollik vaqtini yozadi.

        Chaqiruvchi (`/api/me/activity/track/`) endi FAQAT ish tugagach
        chaqiriladi — diktant oxirigacha yozilganda yoki video testi
        yakunlanganda. Ilgari har soniyada chaqirilardi.
        """
        today = timezone.localdate()
        row, _ = DailyActivity.objects.get_or_create(user=self, date=today)
        if seconds:
            DailyActivity.objects.filter(pk=row.pk).update(seconds=models.F("seconds") + seconds)
        self.last_active_at = timezone.now()
        self.save(update_fields=["last_active_at"])
        # Statistika keshi endi eskirdi — darrov tozalaymiz, aks holda
        # foydalanuvchi videoni tugatib profilga kirsa eski raqamni ko'rardi
        # (ilgari xuddi shu shikoyat bo'lgan).
        from django.core.cache import cache

        cache.delete(stats_cache_key(self.pk))


class DailyActivity(models.Model):
    """Bir kunda qancha vaqt tinglangani — profil grafigi va reyting manbasi."""

    user = models.ForeignKey(
        User, verbose_name="Foydalanuvchi", on_delete=models.CASCADE, related_name="daily_activity"
    )
    date = models.DateField("Sana", db_index=True)
    seconds = models.PositiveIntegerField("Soniya", default=0)

    class Meta:
        verbose_name = "Kunlik faollik"
        verbose_name_plural = "Kunlik faollik"
        unique_together = ("user", "date")
        ordering = ["-date"]

    def __str__(self):
        return f"{self.user} — {self.date} ({self.hours} soat)"

    @property
    def hours(self) -> float:
        return round(self.seconds / 3600, 1)


class TelegramOTP(TimeStampedModel):
    """Bot bergan 1 daqiqalik kirish kodi.

    Kod bot tomonidan yaratiladi (foydalanuvchi botga /start yozadi),
    sayt esa uni tekshirib JWT beradi.
    """

    telegram_id = models.BigIntegerField("Telegram chat ID", db_index=True)
    telegram_username = models.CharField("Telegram username", max_length=64, blank=True)
    first_name = models.CharField("Ism", max_length=120, blank=True)
    code = models.CharField("Kod", max_length=8, db_index=True)
    is_used = models.BooleanField("Ishlatilgan", default=False)
    expires_at = models.DateTimeField("Amal qilish muddati", db_index=True)

    class Meta:
        verbose_name = "Telegram kirish kodi"
        verbose_name_plural = "Telegram kirish kodlari"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.code} → {self.telegram_id}"

    @property
    def is_valid(self) -> bool:
        return not self.is_used and self.expires_at > timezone.now()

    @classmethod
    def purge_expired(cls) -> int:
        """Kerak bo'lmay qolgan kodlarni O'CHIRADI. Nechta o'chirilgani qaytadi.

        Kod atigi 1 daqiqa yashaydi, ya'ni muddati o'tgan yoki allaqachon
        ishlatilgan qator hech qachon qayta kerak bo'lmaydi — jadvalda
        o'tirishi esa faqat zarar: u o'sib boradi va bazada "kirish
        kodlari" tarixi (kim qachon kirmoqchi bo'lgani) keraksiz saqlanadi.

        Chaqiriladi: bot yangi kod berayotganda va sayt kodni tekshirayotganda
        (ya'ni alohida cron kerak emas), hamda `manage.py purge_otp` da.
        """
        now = timezone.now()
        stale = models.Q(expires_at__lt=now) | models.Q(
            is_used=True, created_at__lt=now - timezone.timedelta(minutes=1)
        )
        deleted, _ = cls.objects.filter(stale).delete()
        return deleted


class TestAccountLogin(TimeStampedModel):
    """Doimiy TEST akkauntga har kirilganda bitta yozuv — admin'da ko'rinadi.

    Test kodi (`settings.TEST_OTP_CODE`) bilan kirilganda yoziladi, shu bois
    "test akkauntga qachon kirilgani" tarixi admin'da to'liq ko'rinadi.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="test_logins")
    ip_address = models.GenericIPAddressField("IP manzil", null=True, blank=True)
    user_agent = models.CharField("Qurilma / brauzer", max_length=300, blank=True)

    class Meta:
        verbose_name = "Test akkaunt kirishi"
        verbose_name_plural = "Test akkaunt kirishlari"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user} · {self.created_at:%Y-%m-%d %H:%M}"


class ActiveSession(TimeStampedModel):
    """Har (foydalanuvchi, platforma) uchun BITTA faol sessiya.

    Web'da bitta, mobil'da bitta — ikkalasi bir vaqtda ishlaydi. Bir platformada
    yangi qurilma kirsa, avvalgisi `sid` mos kelmagani uchun avtomatik chiqib
    ketadi (`apps/accounts/auth.py`). `updated_at` — oxirgi kirish vaqti.
    """

    PLATFORMS = ("web", "mobile")

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="sessions")
    platform = models.CharField("Platforma", max_length=12)
    sid = models.CharField("Sessiya ID", max_length=64)
    # Ro'yxatda "qaysi qurilma" ni tanish uchun — foydalanuvchi o'zi ko'radi.
    device = models.CharField("Qurilma / brauzer", max_length=300, blank=True)
    ip_address = models.GenericIPAddressField("IP manzil", null=True, blank=True)
    last_seen_at = models.DateTimeField("Oxirgi so'rov", null=True, blank=True)

    class Meta:
        verbose_name = "Faol sessiya"
        verbose_name_plural = "Faol sessiyalar"
        unique_together = ("user", "platform")
        ordering = ["-updated_at"]

    def __str__(self):
        return f"{self.user} · {self.platform}"

    @property
    def label(self) -> str:
        """User-Agent'dan qisqa, o'qiladigan nom ("Chrome · Windows")."""
        return describe_device(self.device, self.platform)


def describe_device(user_agent: str, platform: str = "web") -> str:
    """User-Agent satrini qisqa nomga aylantiradi.

    To'liq UA foydalanuvchiga hech narsa demaydi (300 belgi shovqin), shu bois
    brauzer + OS ni ajratib olamiz. Topilmasa platformaning o'zi qaytadi.
    """
    ua = user_agent or ""
    low = ua.lower()

    browser = ""
    for needle, name in (
        ("edg/", "Edge"), ("opr/", "Opera"), ("chrome/", "Chrome"),
        ("firefox/", "Firefox"), ("safari/", "Safari"), ("okhttp", "Android"),
        ("expo", "Expo"), ("dart", "Flutter"),
    ):
        if needle in low:
            browser = name
            break

    system = ""
    for needle, name in (
        ("windows", "Windows"), ("android", "Android"), ("iphone", "iPhone"),
        ("ipad", "iPad"), ("mac os", "macOS"), ("linux", "Linux"),
    ):
        if needle in low:
            system = name
            break

    parts = [x for x in (browser, system) if x]
    if parts:
        return " · ".join(parts)
    return "Mobil ilova" if platform == "mobile" else "Brauzer"


class PendingInvite(TimeStampedModel):
    """Botga taklif havolasi bilan kirgan, lekin hali ro'yxatdan o'tmagan odam.

    `t.me/<bot>?start=<invite_code>` bosilganda bot shu qatorni yozadi. Odam
    keyinroq saytga kod bilan kirganda (`TelegramVerifyView`) va u CHINDAN
    yangi foydalanuvchi bo'lsa — taklif hisobga olinadi.

    Nega alohida jadval: /start bosilganda `User` hali mavjud emas (u faqat
    kod tekshirilgach yaratiladi), shu bois bog'lanishni telegram_id ustida
    saqlab turamiz.
    """

    telegram_id = models.BigIntegerField("Telegram chat ID", unique=True, db_index=True)
    inviter = models.ForeignKey(
        User, verbose_name="Kim taklif qilgan", on_delete=models.CASCADE,
        related_name="pending_invites",
    )

    class Meta:
        verbose_name = "Kutilayotgan taklif"
        verbose_name_plural = "Kutilayotgan takliflar"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.telegram_id} ← {self.inviter}"


class Invitation(TimeStampedModel):
    """HISOBGA OLINGAN taklif — bitta yangi foydalanuvchi, bitta qator.

    `invitee` **OneToOne**: bitta odam umri davomida faqat bir marta
    "taklif qilingan" bo'la oladi. Sovg'a hisobi shu jadvalga tayanadi, shu
    bois takrorlanish mumkin emasligi baza darajasida kafolatlanadi —
    kodda xato bo'lsa ham hech kim ikki marta sanalmaydi.
    """

    class Source(models.TextChoices):
        BOT = "bot", "Bot havolasi"
        SETUP = "setup", "Ro'yxatdan o'tishda kod"

    inviter = models.ForeignKey(
        User, verbose_name="Taklif qilgan", on_delete=models.CASCADE, related_name="sent_invites",
    )
    invitee = models.OneToOneField(
        User, verbose_name="Taklif qilingan", on_delete=models.CASCADE, related_name="invite_record",
    )
    source = models.CharField("Manba", max_length=12, choices=Source.choices, default=Source.BOT)
    notified = models.BooleanField("Taklif qilganga xabar berildi", default=False)

    class Meta:
        verbose_name = "Taklif"
        verbose_name_plural = "Takliflar"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["inviter", "created_at"])]

    def __str__(self):
        return f"{self.inviter} → {self.invitee}"

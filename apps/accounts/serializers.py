import re

from django.utils import timezone
from rest_framework import serializers

from apps.common.models import LEVEL_HOURS_REQUIRED

from .models import DailyActivity, User

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,32}$")


def username_error(value: str, current_pk=None) -> str | None:
    """Username formatini + bandligini tekshiradi. Xato bo'lsa matn, aks holda None.

    Bo'sh qiymat — o'zgartirmaslik degani (xato emas).
    """
    v = (value or "").strip()
    if not v:
        return None
    if not USERNAME_RE.match(v):
        return "Username 3–32 belgidan, faqat harf/raqam/_ bo'lishi kerak."
    qs = User.objects.filter(username__iexact=v)
    if current_pk:
        qs = qs.exclude(pk=current_pk)
    if qs.exists():
        return "Bu username band."
    return None


class MeSerializer(serializers.ModelSerializer):
    initial = serializers.CharField(read_only=True)
    invited_count = serializers.IntegerField(read_only=True)
    next_level = serializers.CharField(read_only=True)
    required_hours = serializers.IntegerField(read_only=True)
    invite_link = serializers.SerializerMethodField()
    avatar_url = serializers.SerializerMethodField()
    plan = serializers.SerializerMethodField()
    today_seconds = serializers.SerializerMethodField()
    # Taklif hisobi: `invited_count` — JAMI olib kelgan odam, 
    # `invites_to_next_reward` — keyingi sovg'agacha qolgani.
    invites_to_next_reward = serializers.IntegerField(read_only=True)

    class Meta:
        model = User
        fields = (
            "id", "username", "display_name", "initial", "avatar_url", "telegram_username",
            "cefr_level", "next_level", "required_hours", "gender", "language",
            "last_active_at",
            "invite_code", "invite_link", "invited_count", "invites_to_next_reward",
            "date_joined", "plan", "today_seconds",
        )
        read_only_fields = ("id", "username", "invite_code", "date_joined",
                            "today_seconds")

    def get_invite_link(self, obj):
        """Taklif havolasi — **botga** olib boradi, saytga emas.

        Taklif hisobi bot orqali ishlaydi: odam `?start=<kod>` bilan botga
        kirsa, bot kimning havolasi ekanini yozib qo'yadi va o'sha odam
        ro'yxatdan o'tgach taklif hisobga olinadi
        (`apps/accounts/invites.py`). Sayt havolasida bu ma'lumot
        yo'qolardi.
        """
        from django.conf import settings

        bot = (getattr(settings, "TELEGRAM_BOT_USERNAME", "") or "").lstrip("@")
        if bot:
            return f"https://t.me/{bot}?start={obj.invite_code}"
        # Bot username sozlanmagan bo'lsa — eski sayt havolasi (zaxira).
        return f"{settings.SITE_URL.rstrip('/')}/invite/{obj.invite_code}"

    def get_avatar_url(self, obj):
        if not obj.avatar:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(obj.avatar.url) if request else obj.avatar.url

    def get_plan(self, obj):
        subscription = obj.subscriptions.select_related("plan").first()
        if subscription and subscription.is_active:
            return subscription.plan.code
        return "free"

    def get_today_seconds(self, obj) -> int:
        from django.utils import timezone
        today = timezone.localdate()
        row = obj.daily_activity.filter(date=today).first()
        return row.seconds if row else 0


# Avatar cheklovlari — mijoz (mobil/sayt) ham shu qiymatlarga tayanadi.
AVATAR_MAX_BYTES = 5 * 1024 * 1024          # 5 MB
AVATAR_MAX_SIDE = 4096                      # px — bundan kattasi keraksiz
AVATAR_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


class MeUpdateSerializer(serializers.ModelSerializer):
    username = serializers.CharField(required=False, allow_blank=True)

    class Meta:
        model = User
        fields = ("display_name", "cefr_level", "gender", "language", "avatar", "username")

    def validate_avatar(self, value):
        """Avatar: HAQIQIY rasm bo'lsin va hajmi cheklangan bo'lsin.

        `ImageField` o'zi Pillow bilan "rasmmi?" ni tekshiradi (nomi `.jpg`
        bo'lgan matn fayl o'tmaydi), lekin **hajmni tekshirmaydi** — kimdir
        200 MB fayl yuklab diskni to'ldirishi mumkin edi. Shu bois:

          - hajm  ≤ 5 MB
          - format jpeg / png / webp / gif
          - tomoni ≤ 4096 px (undan kattasi avatar uchun keraksiz)

        Eslatma: Django `ImageField` `content_type` ni **rasmning o'zidan**
        qayta aniqlaydi, ya'ni tekshiruv mijoz bergan yorliqqa emas, haqiqiy
        formatga qaraydi (PNG ni "application/pdf" deb yuborib aldab
        bo'lmaydi).
        """
        if value is None:
            return value

        size = getattr(value, "size", 0) or 0
        if size > AVATAR_MAX_BYTES:
            mb = AVATAR_MAX_BYTES // (1024 * 1024)
            raise serializers.ValidationError(
                f"Rasm hajmi {mb} MB dan oshmasligi kerak "
                f"(sizniki {size / 1024 / 1024:.1f} MB)."
            )

        content_type = (getattr(value, "content_type", "") or "").lower()
        if content_type and content_type not in AVATAR_CONTENT_TYPES:
            raise serializers.ValidationError(
                "Faqat rasm yuklash mumkin (JPG, PNG, WEBP yoki GIF)."
            )

        # `ImageField` allaqachon ochib ko'rgan — o'lchamlari shu yerda.
        dims = getattr(value, "image", None)
        if dims is not None and max(dims.size) > AVATAR_MAX_SIDE:
            raise serializers.ValidationError(
                f"Rasm juda katta — tomoni {AVATAR_MAX_SIDE}px dan oshmasin."
            )
        return value

    def validate_username(self, value):
        current_pk = self.instance.pk if self.instance else None
        err = username_error(value, current_pk)
        if err:
            raise serializers.ValidationError(err)
        # Bo'sh bo'lsa — mavjudini saqlaymiz (o'zgartirmaymiz).
        return (value or "").strip() or (self.instance.username if self.instance else value)


class DailyActivitySerializer(serializers.ModelSerializer):
    hours = serializers.FloatField(read_only=True)

    class Meta:
        model = DailyActivity
        fields = ("date", "seconds", "hours")


class StatsSerializer(serializers.Serializer):
    """Profil sahifasidagi barcha raqamlar."""

    join_date = serializers.DateField()
    active_days = serializers.IntegerField()
    last_active_hours_ago = serializers.IntegerField(allow_null=True)
    # Yaxlit soatlar (backward compat)
    active_time_hours = serializers.FloatField()
    last7_hours = serializers.FloatField()
    last30_hours = serializers.FloatField()
    # Aniq soniyalar — frontend "45s" / "12m" / "1h 23m" formatida ko'rsatadi
    active_time_seconds = serializers.IntegerField()
    last7_seconds = serializers.IntegerField()
    last30_seconds = serializers.IntegerField()
    level = serializers.CharField()
    next_level = serializers.CharField()
    required_hours = serializers.IntegerField()
    level_progress_percent = serializers.IntegerField()

    @staticmethod
    def build(user) -> dict:
        total_sec = int(user.total_seconds or 0)
        last7_sec = int(user.seconds_in_period(7) or 0)
        last30_sec = int(user.seconds_in_period(30) or 0)
        total_hours = round(total_sec / 3600, 1)
        required = LEVEL_HOURS_REQUIRED.get(user.cefr_level, 40)
        last_active_hours = None
        if user.last_active_at:
            delta = timezone.now() - user.last_active_at
            last_active_hours = int(delta.total_seconds() // 3600)
        return {
            "join_date": user.date_joined.date(),
            "active_days": user.daily_activity.filter(seconds__gt=0).count(),
            "last_active_hours_ago": last_active_hours,
            "active_time_hours": total_hours,
            "last7_hours": round(last7_sec / 3600, 1),
            "last30_hours": round(last30_sec / 3600, 1),
            "active_time_seconds": total_sec,
            "last7_seconds": last7_sec,
            "last30_seconds": last30_sec,
            "level": user.cefr_level,
            "next_level": user.next_level,
            "required_hours": required,
            "level_progress_percent": min(100, int(total_hours / required * 100)) if required else 0,
        }


class LeaderboardRowSerializer(serializers.Serializer):
    rank = serializers.IntegerField()
    user_id = serializers.IntegerField()
    name = serializers.CharField()
    username = serializers.CharField(allow_blank=True, required=False)
    initial = serializers.CharField()
    # Foydalanuvchi rasm qo'ygan bo'lsa reytingda ham ko'rinsin (bo'lmasa
    # `initial` harfi ishlatiladi).
    avatar_url = serializers.CharField(allow_null=True, required=False)
    hours = serializers.FloatField()
    is_me = serializers.BooleanField()


class OtpVerifySerializer(serializers.Serializer):
    code = serializers.CharField(min_length=4, max_length=8)


class ProfileSetupSerializer(serializers.Serializer):
    display_name = serializers.CharField(max_length=120)
    cefr_level = serializers.CharField(max_length=2)
    # Username — ixtiyoriy. Default Telegram username'dan olinadi (frontend
    # prefill qiladi). Bandligi ProfileSetupView'da tekshiriladi.
    username = serializers.CharField(max_length=32, required=False, allow_blank=True)
    # Interfeys tili — yangi foydalanuvchi ro'yxatdan o'tayotganda o'zi
    # tanlaydi. Faqat ikkita til bor: uz / en.
    language = serializers.ChoiceField(choices=["uz", "en"], required=False)
    invite_code = serializers.CharField(required=False, allow_blank=True)

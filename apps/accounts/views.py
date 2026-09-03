"""Autentifikatsiya (Telegram OTP) va profil API'si."""
from django.conf import settings
from django.db.models import Sum
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from apps.common.models import CEFR

from .models import DailyActivity, TelegramOTP, TestAccountLogin, User


def client_ip(request) -> str | None:
    """nginx ortidagi haqiqiy IP (X-Forwarded-For), aks holda REMOTE_ADDR."""
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")
from .serializers import (
    DailyActivitySerializer, LeaderboardRowSerializer, MeSerializer, MeUpdateSerializer,
    OtpVerifySerializer, ProfileSetupSerializer, StatsSerializer,
)


def issue_tokens(user, request=None) -> dict:
    # Sessiyaga bog'liq (bir platformada bitta qurilma) — `apps/accounts/auth.py`.
    from .auth import build_tokens
    return build_tokens(user, request)


class TelegramVerifyView(APIView):
    """Botdan olingan 6 xonali kodni JWT'ga almashtiradi.

    Kod 1 daqiqa yashaydi, shuning uchun bu endpoint qattiq throttle
    qilingan (5/min) — aks holda kodni brute-force qilish mumkin.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "otp_verify"

    def _test_accounts(self) -> dict:
        return getattr(settings, "TEST_ACCOUNTS", {}) or {}

    def get_throttles(self):
        # Doimiy test kodlari rate-limitga tushmaydi (istalgan paytda kirish).
        code = str((getattr(self.request, "data", None) or {}).get("code", "")).strip()
        if code and code in self._test_accounts():
            return []
        return super().get_throttles()

    def post(self, request):
        serializer = OtpVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        code = serializer.validated_data["code"].strip()

        # ── Doimiy TEST akkauntlar: kod `settings.TEST_ACCOUNTS` da bo'lsa har
        #    doim mos akkauntga (o'z tarifida) kiradi; kirish admin'da yoziladi. ──
        accounts = self._test_accounts()
        if code in accounts:
            username, plan_code = accounts[code]
            display = username.replace("_", " ").title()
            user, created = User.objects.get_or_create(
                username=username,
                defaults={"display_name": display, "cefr_level": CEFR.B1},
            )
            self._ensure_plan(user, plan_code)
            user.touch_activity()
            TestAccountLogin.objects.create(
                user=user,
                ip_address=client_ip(request),
                user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:300],
            )
            return Response({
                **issue_tokens(user, request),
                "is_new": created,
                "needs_setup": created or not user.display_name,
                "user": MeSerializer(user, context={"request": request}).data,
            })

        # ── Captcha (hCaptcha) — HAQIQIY foydalanuvchilar uchun MAJBURIY ──
        #    Test kodlari yuqorida qaytib ketdi (ular captchasiz). Secret bo'sh
        #    bo'lsa (lokal) captcha o'chiq — `verify_captcha` doim True.
        from .captcha import verify_captcha
        token = str(request.data.get("captcha_token") or "").strip()
        if not verify_captcha(token, client_ip(request)):
            return Response(
                {"error": {"code": "captcha_failed",
                           "message": "Captcha tasdiqlanmadi. Qayta urinib ko'ring."}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Kerak bo'lmay qolgan kodlarni shu yerda tozalaymiz (cron kerak emas):
        # kod 1 daqiqa yashaydi, muddati o'tgani jadvalda turishining foydasi yo'q.
        TelegramOTP.purge_expired()

        otp = (
            TelegramOTP.objects
            .filter(code=code, is_used=False, expires_at__gt=timezone.now())
            .order_by("-created_at")
            .first()
        )
        if otp is None:
            return Response(
                {"detail": "Kod noto'g'ri yoki muddati tugagan"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user, created = User.objects.get_or_create(
            telegram_id=otp.telegram_id,
            defaults={
                "username": f"tg{otp.telegram_id}",
                "display_name": otp.first_name or "",
                "telegram_username": otp.telegram_username or "",
            },
        )
        if not created and otp.telegram_username and user.telegram_username != otp.telegram_username:
            user.telegram_username = otp.telegram_username
            user.save(update_fields=["telegram_username"])

        # Yangi foydalanuvchiga default username — Telegram username'idan (band
        # bo'lmasa). Foydalanuvchi keyin setup ekranida o'zgartira oladi.
        if created and otp.telegram_username:
            from .serializers import username_error
            if username_error(otp.telegram_username, user.pk) is None:
                user.username = otp.telegram_username.strip()
                user.save(update_fields=["username"])

        TelegramOTP.objects.filter(pk=otp.pk).update(is_used=True)
        user.touch_activity()

        # Botga taklif havolasi bilan kirgan bo'lsa — endi hisobga olamiz.
        #
        # HAR kirishda chaqiriladi, faqat `created` da emas. Sabab: havola
        # ro'yxatdan o'tgandan KEYIN ham bosilishi mumkin (kod 60 s da
        # eskiraydi, odam bir necha marta urinadi va tartib chalkashadi) —
        # ilgari bunday `PendingInvite` hech qachon o'qilmay yotib qolardi.
        #
        # Bu qo'sh hisobga olib kelmaydi: barcha tekshiruv
        # `register_invitation` da — `Invitation.invitee` OneToOne (bir odam
        # bir marta) va `NEW_USER_WINDOW` (eski akkaunt sanalmaydi).
        from .invites import attach_pending_invite
        attach_pending_invite(user)

        needs_setup = created or not user.display_name
        return Response({
            **issue_tokens(user, request),
            "is_new": created,
            "needs_setup": needs_setup,
            "user": MeSerializer(user, context={"request": request}).data,
        })

    @staticmethod
    def _ensure_plan(user, plan_code: str) -> None:
        """Test akkauntга mos FAOL obunani ta'minlaydi (free — obuna kerak emas)."""
        if plan_code == "free":
            return
        from apps.billing.grants import grant_plan
        from apps.billing.models import Plan, Reason, Subscription
        plan = Plan.objects.filter(code=plan_code).first()
        if not plan:
            return
        if not Subscription.objects.filter(user=user, plan=plan, status=Subscription.Status.ACTIVE).exists():
            Subscription.objects.filter(user=user).delete()
            # months=0 → muddatsiz (test akkaunt doim shu tarifda bo'lsin).
            grant_plan(user, plan, 0, Reason.TEST, note="Test akkaunt")


class ProfileSetupView(APIView):
    """Ro'yxatdan o'tgandan keyingi "Profilni to'ldiring" ekrani."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = ProfileSetupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        user = request.user
        user.display_name = data["display_name"]
        if data["cefr_level"] in CEFR.values:
            user.cefr_level = data["cefr_level"]
        # Til tanlangan bo'lsa saqlaymiz — keyingi kirishlarda frontend
        # `me.language` ni o'qib interfeysni o'sha tilda ochadi.
        if data.get("language") in ("uz", "en"):
            user.language = data["language"]

        # Taklif kodi qo'lda kiritilgan bo'lsa — bot havolasidagi bilan bir xil
        # yo'ldan o'tadi (`invites.register_invitation`): tekshiruv, bir marta
        # hisoblash, xabar va sovg'a hammasi o'sha yerda.
        invite_code = (data.get("invite_code") or "").strip().upper()
        if invite_code and not user.invited_by_id:
            from .invites import find_inviter, register_invitation
            from .models import Invitation
            inviter = find_inviter(invite_code)
            if inviter and inviter.pk != user.pk:
                register_invitation(inviter, user, Invitation.Source.SETUP)
                user.refresh_from_db(fields=["invited_by"])

        fields = ["display_name", "cefr_level", "language", "invited_by"]
        # Username (ixtiyoriy) — berilgan bo'lsa bandligini tekshiramiz.
        uname = (data.get("username") or "").strip()
        if uname:
            from .serializers import username_error
            err = username_error(uname, user.pk)
            if err:
                return Response({"error": {"code": "username_taken", "message": err}},
                                status=status.HTTP_400_BAD_REQUEST)
            user.username = uname
            fields.append("username")

        user.save(update_fields=fields)

        # Ro'yxatdan o'tish ENDI tugadi (ism + daraja bor) — kutilayotgan
        # taklif shu yerda hisobga olinadi. `TelegramVerifyView` da emas:
        # kod bilan kirishning o'zi yetarli emas (foydalanuvchi talabi).
        from .invites import attach_pending_invite
        attach_pending_invite(user)

        return Response(MeSerializer(user, context={"request": request}).data)


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(MeSerializer(request.user, context={"request": request}).data)

    def patch(self, request):
        serializer = MeUpdateSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(MeSerializer(request.user, context={"request": request}).data)


#: Statistika keshi — yozuvda tozalanadi, TTL faqat zaxira.
STATS_TTL = 300


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def my_stats(request):
    """Profil raqamlari — KESHLANGAN (yozuvda darrov tozalanadi).

    To'rtta og'ir agregat (jami / 7 kun / 30 kun / faol kunlar) va profil
    ekrani har fokuslanganda so'raydi. `touch_activity()` yozganda kesh
    o'chiriladi, ya'ni raqam hech qachon eskirmaydi.
    """
    from django.core.cache import cache

    from .models import stats_cache_key

    key = stats_cache_key(request.user.pk)
    data = cache.get(key)
    if data is None:
        data = StatsSerializer.build(request.user)
        cache.set(key, data, STATS_TTL)
    return Response(data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def my_limits(request):
    """Joriy tarif + har tur bo'yicha kunlik limit/ishlatilgan/qolgan."""
    from apps.billing.limits import snapshot
    return Response(snapshot(request.user))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def my_invites(request):
    """Taklif statistikasi + oxirgi sovg'alar (profil sahifasi uchun)."""
    from apps.billing.rewards import reward_progress

    data = reward_progress(request.user)
    rewards = request.user.invite_rewards.select_related("plan").all()[:20]
    data["rewards"] = [
        {
            "id": r.id,
            "plan": r.plan.code,
            "plan_name_uz": r.plan.name_uz,
            "plan_name_en": r.plan.name_en,
            "months": r.months,
            "invites_spent": r.invites_spent,
            "created_at": r.created_at,
        }
        for r in rewards
    ]
    return Response(data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def my_subscriptions(request):
    """Tarif TARIXI — qachon, qaysi tarif, qanday yo'l bilan, qachongacha.

    Faqat saytda ko'rsatiladi (mobil ilovada kerak emas). Yozuvlar
    o'zgarmas: `apps/billing/models.py::SubscriptionEvent`.
    """
    from apps.billing.grants import current_subscription

    events = request.user.subscription_events.select_related("plan").all()[:50]
    current = current_subscription(request.user)
    return Response({
        "current": None if current is None else {
            "plan": current.plan.code,
            "plan_name_uz": current.plan.name_uz,
            "plan_name_en": current.plan.name_en,
            "status": current.status,
            "reason": current.reason,
            "started_at": current.started_at,
            "expires_at": current.expires_at,
            "is_active": current.is_active,
        },
        "results": [
            {
                "id": e.id,
                "plan": e.plan.code,
                "plan_name_uz": e.plan.name_uz,
                "plan_name_en": e.plan.name_en,
                "reason": e.reason,
                "reason_label": e.get_reason_display(),
                "months": e.months,
                "started_at": e.started_at,
                "expires_at": e.expires_at,
                "note": e.note,
                "created_at": e.created_at,
            }
            for e in events
        ],
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def username_check(request):
    """`?username=...` — band emasligini tekshiradi (jonli, setup ekranida)."""
    from .serializers import username_error
    value = (request.query_params.get("username") or "").strip()
    err = username_error(value, request.user.pk)
    return Response({"available": bool(value) and err is None, "username": value, "error": err})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def my_activity(request):
    """Profil sahifasidagi kunlik faollik grafigi."""
    days = int(request.query_params.get("days", 14))
    since = timezone.localdate() - timezone.timedelta(days=days - 1)
    rows = {r.date: r for r in request.user.daily_activity.filter(date__gte=since)}

    result = []
    for offset in range(days):
        day = timezone.localdate() - timezone.timedelta(days=offset)
        row = rows.get(day)
        result.append({"date": day, "seconds": row.seconds if row else 0,
                       "hours": row.hours if row else 0.0})
    return Response(result)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def track_activity(request):
    """Tinglash vaqti hisobiga qo'shiladi.

    **Muhim qoida:** frontend buni FAQAT ish tugagach chaqiradi — diktant
    oxirigacha yozib bo'linganda yoki video testi yakunlanganda. Qo'shiladigan
    qiymat audio/video to'liq davomiyligi. Ilgari har soniyada chaqirilardi;
    endi tugatilmagan ish umuman hisoblanmaydi.

    Javobda bugungi umumiy soniya qaytariladi (navbardagi indikator uchun).
    """
    seconds = max(0, min(int(request.data.get("seconds", 0)), 3600))
    if seconds > 0:
        request.user.touch_activity(seconds)

    from django.utils import timezone
    today = timezone.localdate()
    row = request.user.daily_activity.filter(date=today).first()
    return Response({
        "ok": True,
        "today_seconds": row.seconds if row else 0,
    })


@api_view(["GET"])
@permission_classes([AllowAny])
def leaderboard(request):
    """TOP 30 — so'nggi 7 yoki 30 kun bo'yicha faol vaqt.

    OG'IR aggregatsiya (barcha DailyActivity yig'indisi) foydalanuvchiga
    bog'liq EMAS — 60s keshlanadi. `is_me` faqat keshlangan ro'yxatga
    so'rovda qo'shiladi (arzon), shu bois javob har user uchun to'g'ri.
    """
    from django.core.cache import cache
    period = 30 if request.query_params.get("period") == "30" else 7
    since = timezone.localdate() - timezone.timedelta(days=period - 1)

    cache_key = f"leaderboard_rows_{period}_{since.isoformat()}"
    data = cache.get(cache_key)
    if data is None:
        rows = (
            DailyActivity.objects.filter(date__gte=since, seconds__gt=0)
            .values("user_id", "user__display_name", "user__username", "user__avatar")
            .annotate(total=Sum("seconds"))
            .order_by("-total")[:30]
        )
        data = []
        for index, row in enumerate(rows, start=1):
            name = row["user__display_name"] or row["user__username"]
            data.append({
                "rank": index,
                "user_id": row["user_id"],
                "name": name,
                "username": row["user__username"] or "",
                "initial": (name or "?")[:1].upper(),
                "hours": round(row["total"] / 3600, 1),
                # Keshda NISBIY yo'l saqlanadi ("avatars/x.jpg"). To'liq URL
                # har so'rovda quriladi — aks holda kesh bitta host'ni
                # yodda saqlab qolardi (LAN IP / domen aralashib ketardi).
                "avatar": row["user__avatar"] or "",
            })
        cache.set(cache_key, data, 60)

    from django.conf import settings
    me_id = request.user.id if request.user.is_authenticated else None

    def absolute(rel: str) -> str | None:
        if not rel:
            return None
        return request.build_absolute_uri(f"{settings.MEDIA_URL}{rel}")

    # `is_me` va `avatar_url` — keshlangan ro'yxatga har so'rovda qo'shiladi
    # (keshning o'zi buzilmaydi).
    data = [
        {
            **{k: v for k, v in row.items() if k != "avatar"},
            "is_me": row["user_id"] == me_id,
            "avatar_url": absolute(row.get("avatar", "")),
        }
        for row in data
    ]

    my_hours = 0.0
    if me_id:
        my_hours = round(request.user.seconds_in_period(period) / 3600, 1)

    return Response({
        "period": period,
        "my_hours": my_hours,
        "results": LeaderboardRowSerializer(data, many=True).data,
    })

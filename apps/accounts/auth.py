"""Sessiyaga bog'liq JWT — har (foydalanuvchi, platforma) uchun BITTA faol sessiya.

Web va mobil bir vaqtda ishlaydi (har biri bitta). Bir platformada yangi
qurilma kirsa, tokenidagi `sid` `ActiveSession` dagidan farq qiladi va avvalgi
qurilma darrov chiqib ketadi (401 → mijoz signout qiladi).

## Qat'iy tekshiruv (QASDDAN)

`session_ok` faqat `sid` **aynan mos kelganda** ruxsat beradi. Ilgari ikkita
yumshatish bor edi va ikkalasi ham teshik edi:

  - `sid` yo'q token (bu funksiya qo'shilishidan oldin berilgan) — **abadiy**
    amal qilardi, ya'ni eski qurilma hech qachon chiqmasdi. Aynan shu sabab
    "bitta akkauntda bir nechta mobil sessiya" ko'rinardi.
  - `ActiveSession` qatori yo'q bo'lsa "mayli" deb o'tkazardi — bu esa
    sessiyani QO'LDA chiqarishni (foydalanuvchi "Bu qurilmani chiqarish"
    tugmasini bossa) ishlamas qilardi: qator o'chgach token yana ishlayverardi.

Endi ikkalasi ham 401. Yagona narxi — bu o'zgarish chiqqanda hamma bir marta
qayta kirishi kerak bo'ladi.

MUHIM: bu modul FAQAT `JWTAuthentication` ni import qiladi. `TokenRefreshView`
(rest_framework.views ni tortadi) `refresh.py` da — aks holda DRF
`DEFAULT_AUTHENTICATION_CLASSES` ni yechishda aylanma import bo'ladi.
"""
import uuid
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed
from rest_framework_simplejwt.tokens import RefreshToken

#: `last_seen_at` shu oraliqdan tez-tez yozilmaydi — har so'rovda UPDATE
#: qilish bazaga keraksiz yuk (sessiya ro'yxatida daqiqa aniqligi yetarli).
LAST_SEEN_THROTTLE = timedelta(minutes=5)


def platform_of(request) -> str:
    """`X-Platform` sarlavhasidan: 'mobile' yoki (default) 'web'."""
    p = (request.headers.get("X-Platform") or "").lower() if request is not None else ""
    return "mobile" if p == "mobile" else "web"


def client_ip(request):
    """nginx ortidagi haqiqiy IP (X-Forwarded-For), aks holda REMOTE_ADDR."""
    if request is None:
        return None
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def build_tokens(user, request=None) -> dict:
    """sid + platforma bilan access/refresh yaratadi, `ActiveSession` ni yangilaydi."""
    from .models import ActiveSession

    refresh = RefreshToken.for_user(user)
    plat = platform_of(request)
    sid = uuid.uuid4().hex
    refresh["sid"] = sid
    refresh["plat"] = plat
    access = refresh.access_token
    access["sid"] = sid
    access["plat"] = plat

    ActiveSession.objects.update_or_create(
        user=user,
        platform=plat,
        defaults={
            "sid": sid,
            "device": (request.META.get("HTTP_USER_AGENT") or "")[:300] if request else "",
            "ip_address": client_ip(request),
            "last_seen_at": timezone.now(),
        },
    )
    return {"access": str(access), "refresh": str(refresh)}


def session_ok(user_id, plat, sid) -> bool:
    """Token shu foydalanuvchining JORIY sessiyasiga tegishlimi."""
    if not sid or not plat:
        return False  # eski (sid'siz) token — qayta kirish kerak
    from .models import ActiveSession

    current = (
        ActiveSession.objects
        .filter(user_id=user_id, platform=plat)
        .values_list("sid", flat=True)
        .first()
    )
    return current == sid


def touch_session(user_id, plat, sid, request=None) -> None:
    """Sessiya ro'yxatidagi "oxirgi so'rov" vaqtini yangilaydi (5 daqiqada bir)."""
    from .models import ActiveSession

    now = timezone.now()
    fields = {"last_seen_at": now}
    ip = client_ip(request)
    if ip:
        fields["ip_address"] = ip
    # Bitta UPDATE: eskirgan yoki hech qachon yozilmagan (NULL) qatorlar.
    ActiveSession.objects.filter(
        Q(last_seen_at__isnull=True) | Q(last_seen_at__lt=now - LAST_SEEN_THROTTLE),
        user_id=user_id, platform=plat, sid=sid,
    ).update(**fields)


class SessionJWTAuthentication(JWTAuthentication):
    """DRF autentifikatsiyasi — har so'rovda token `sid` ni ActiveSession bilan solishtiradi."""

    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        plat = validated_token.get("plat")
        sid = validated_token.get("sid")
        if not session_ok(user.id, plat, sid):
            raise AuthenticationFailed("Boshqa qurilmada kirildi", code="session_superseded")
        try:
            touch_session(user.id, plat, sid, getattr(self, "_request", None))
        except Exception:
            pass  # statistik maydon — asosiy oqimni buzmasin
        return user

    def authenticate(self, request):
        # `get_user` ga so'rovni yetkazish uchun (IP yangilash).
        self._request = request
        return super().authenticate(request)

"""Kirish nazorati: IP ro'yxati + HTTP Basic autentikatsiya.

Ikki qatlam ATAYLAB alohida:

1. **IP** — Приложение №2, 4.2-band: Paynet faqat `213.230.106.112/28` va
   `213.230.65.80/28` dan keladi, va 4.3-band bizni boshqa IP'dan
   **to'lovni qabul qilmaslikka MAJBURLAYDI**. Notanish IP → 601.
2. **Basic auth** — UWS_JSON 2.2-band: login/parol bo'lmasa yoki noto'g'ri
   bo'lsa **HTTP 401** (JSON-RPC xatosi emas, aynan HTTP statusi).

Tartib muhim: IP tekshiruvi BIRINCHI. Notanish manzilga parol to'g'rimi-yo'q
degan ma'lumotni ham bermaymiz.
"""
import base64
import ipaddress
import secrets

from django.conf import settings
from django.contrib.auth.hashers import check_password

from . import errors
from .models import PaynetCredential


def client_ip(request) -> str:
    """So'rovning HAQIQIY manbasi.

    nginx `X-Real-IP` ni HAR DOIM `$remote_addr` bilan qayta yozadi
    (`nginx/proxy_params_listening`), shu bois uni tashqaridan soxtalashtirib
    bo'lmaydi. Sarlavha umuman bo'lmasa (nginx'siz dev) `REMOTE_ADDR`.
    """
    return (request.META.get("HTTP_X_REAL_IP") or request.META.get("REMOTE_ADDR") or "").strip()


def _networks():
    nets = []
    for raw in getattr(settings, "PAYNET_ALLOWED_NETS", []):
        raw = str(raw).strip()
        if not raw:
            continue
        try:
            nets.append(ipaddress.ip_network(raw, strict=False))
        except ValueError:
            # Noto'g'ri yozilgan qator butun ro'yxatni yiqitmasin — qolganlari
            # baribir ishlaydi (va bo'sh ro'yxat degani "hammaga ochiq" emas,
            # pastdagi `ip_allowed` ga qarang).
            continue
    return nets


def ip_allowed(request) -> bool:
    """IP ruxsat etilganmi.

    `PAYNET_ALLOWED_NETS` bo'sh bo'lsa tekshiruv O'CHIQ — bu faqat `DEBUG`
    rejimida (lokal test) mumkin. Prod'da bo'sh ro'yxat = hech kim kira
    olmaydi: xavfsizlik sozlamasini unutib qoldirish "hammaga ruxsat" ga
    aylanmasligi kerak.
    """
    nets = _networks()
    if not nets:
        return bool(settings.DEBUG)
    try:
        addr = ipaddress.ip_address(client_ip(request))
    except ValueError:
        return False
    return any(addr in net for net in nets)


def _expected_password_ok(candidate: str) -> bool:
    """Parol to'g'rimi: avval bazadagi (ChangePassword yozgan), keyin env."""
    row = PaynetCredential.objects.filter(pk=1).first()
    if row:
        return check_password(candidate, row.password_hash)
    expected = getattr(settings, "PAYNET_PASSWORD", "")
    return bool(expected) and secrets.compare_digest(candidate, expected)


def basic_auth_ok(request) -> bool:
    """`Authorization: Basic ...` sarlavhasi to'g'rimi."""
    header = request.META.get("HTTP_AUTHORIZATION", "")
    if not header.startswith("Basic "):
        return False
    try:
        raw = base64.b64decode(header[6:].strip()).decode("utf-8")
        login, _, password = raw.partition(":")
    except (ValueError, UnicodeDecodeError):
        return False

    expected_login = getattr(settings, "PAYNET_LOGIN", "")
    if not expected_login:
        return False
    # `compare_digest` — login taqqoslashda ham vaqt bo'yicha oqishni yopadi.
    if not secrets.compare_digest(login, expected_login):
        return False
    return _expected_password_ok(password)


def set_password(new_password: str) -> None:
    """`ChangePassword` metodi chaqiradi — parolni bazaga hash qilib yozadi."""
    from django.contrib.auth.hashers import make_password

    PaynetCredential.objects.update_or_create(
        pk=1, defaults={"password_hash": make_password(new_password)},
    )


def service_allowed(service_id) -> int:
    """`serviceId` bizniki ekanini tasdiqlaydi va butun songa aylantiradi.

    `PAYNET_SERVICE_IDS` bo'sh bo'lsa har qanday ID qabul qilinadi (shartnoma
    imzolangunicha Paynet ID'ni bermaydi). Prod'da to'ldirilishi shart —
    aks holda begona servisning to'lovi bizga yozilib qolishi mumkin.
    """
    try:
        sid = int(service_id)
    except (TypeError, ValueError):
        raise errors.PaynetError(errors.SERVICE_NOT_FOUND)
    allowed = getattr(settings, "PAYNET_SERVICE_IDS", [])
    if allowed and sid not in {int(x) for x in allowed}:
        raise errors.PaynetError(errors.SERVICE_NOT_FOUND)
    return sid

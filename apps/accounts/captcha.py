"""Cloudflare Turnstile — sayt login'i (OTP verify) uchun captcha.

Rasm ham, matnli topshiriq ham yo'q: ko'pincha o'zi tekshirib o'tkazadi,
kerak bo'lsa bitta katak bosiladi. Tekin va cheksiz (sayt allaqachon
Cloudflare ortida). Ilgari hCaptcha edi — uning "naqshni buzgan 3 ta narsani
tanlang" jumboqlari ingliz tilini yaxshi bilmaganlar uchun tushunarsiz edi;
reCAPTCHA esa oyiga 10 000 tekshiruvdan keyin pullik.

**Yoqilishi:** `TURNSTILE_SECRET` VA `TURNSTILE_SITEKEY` ikkalasi ham berilsa.
Biri bo'sh bo'lsa captcha O'CHIQ (lokal ishlab chiqish, testlar). Sitekey
saytga `/api/config/` orqali beriladi — frontend build'iga kirmaydi, ya'ni
kalit almashsa frontend'ni qayta yig'ish shart emas.
"""
import json
import urllib.parse
import urllib.request

from django.conf import settings

_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def captcha_enabled() -> bool:
    return bool(getattr(settings, "TURNSTILE_SECRET", "") and getattr(settings, "TURNSTILE_SITEKEY", ""))


def public_sitekey() -> str:
    """Sayt widget'i uchun kalit; captcha o'chiq bo'lsa bo'sh."""
    return settings.TURNSTILE_SITEKEY if captcha_enabled() else ""


def verify_captcha(token: str, remote_ip: str | None = None) -> bool:
    """Turnstile token'ini Cloudflare bilan tekshiradi. O'chiq bo'lsa — doim True.

    Tarmoq xatosi bo'lsa `False` (xavfsiz tomon — captcha o'tmagan hisoblanadi),
    lekin qattiq timeout (5s) bilan — login cheksiz osilib qolmaydi.
    """
    if not captcha_enabled():
        return True
    if not token:
        return False
    data = {"secret": settings.TURNSTILE_SECRET, "response": token}
    if remote_ip:
        data["remoteip"] = remote_ip
    body = urllib.parse.urlencode(data).encode()
    try:
        req = urllib.request.Request(_VERIFY_URL, data=body)
        with urllib.request.urlopen(req, timeout=5) as resp:
            result = json.loads(resp.read().decode())
        return bool(result.get("success"))
    except Exception:
        return False

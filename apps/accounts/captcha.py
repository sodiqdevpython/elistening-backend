"""hCaptcha tekshiruvi — login (OTP verify) uchun.

hCaptcha "mashina / svetafor / velosiped topish" uslubidagi rasm-jumboqni
ko'rsatadi (bepul, GDPR-do'st). Frontend token oladi, backend uni hCaptcha
serveri bilan tekshiradi.

**Yoqilishi:** `HCAPTCHA_SECRET` bo'sh bo'lsa captcha O'CHIQ (lokal ishlab
chiqish qulay bo'lsin). Prod'da `.env` da secret berilsa — MAJBURIY bo'ladi.
"""
import urllib.parse
import urllib.request

from django.conf import settings

_VERIFY_URL = "https://api.hcaptcha.com/siteverify"


def captcha_enabled() -> bool:
    return bool(getattr(settings, "HCAPTCHA_SECRET", ""))


def verify_captcha(token: str, remote_ip: str | None = None) -> bool:
    """hCaptcha token'ini tekshiradi. Secret bo'lmasa (o'chiq) — doim True.

    Tarmoq xatosi bo'lsa `False` (xavfsiz tomon — captcha o'tmagan hisoblanadi),
    lekin qattiq timeout (5s) bilan — login cheksiz osilib qolmaydi.
    """
    secret = getattr(settings, "HCAPTCHA_SECRET", "")
    if not secret:
        return True  # captcha o'chiq
    if not token:
        return False
    data = {"secret": secret, "response": token}
    if remote_ip:
        data["remoteip"] = remote_ip
    body = urllib.parse.urlencode(data).encode()
    try:
        req = urllib.request.Request(_VERIFY_URL, data=body)
        with urllib.request.urlopen(req, timeout=5) as resp:
            import json
            result = json.loads(resp.read().decode())
        return bool(result.get("success"))
    except Exception:
        return False

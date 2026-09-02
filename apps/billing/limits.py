"""Kunlik limit mantig'i — web va mobil bir xil shu yerdan foydalanadi.

Turlar (`kind`): `shorts`, `video`, `dictation`, `ielts`.
Limit: `None` = cheksiz, `0` = umuman mumkin emas, N = kuniga N ta noyob kontent.
"""
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response

from .models import DailyUsage, Plan

KINDS = DailyUsage.KINDS
_NOTIFY_KIND = "_limit_notified"  # bir kunda bir marta bot SMS uchun belgisi


def get_user_plan(user) -> Plan | None:
    """Foydalanuvchining joriy tarifi (faol obuna) yoki standart (free)."""
    sub = user.subscriptions.select_related("plan").first()
    if sub and sub.is_active:
        return sub.plan
    return Plan.objects.filter(is_default=True).first() or Plan.objects.filter(code="free").first()


def used_today(user, kind: str) -> int:
    return DailyUsage.objects.filter(user=user, date=timezone.localdate(), kind=kind).count()


def snapshot(user, plan: Plan | None = None) -> dict:
    """Profil / limit ekrani uchun: tarif + har tur bo'yicha limit/used/remaining."""
    plan = plan or get_user_plan(user)
    limits = {}
    for k in KINDS:
        lim = plan.limit_for(k) if plan else None
        used = used_today(user, k)
        limits[k] = {
            "limit": lim,
            "used": used,
            "remaining": None if lim is None else max(0, lim - used),
        }
    return {
        "plan": plan.code if plan else "free",
        "plan_name_uz": plan.name_uz if plan else "Free",
        "plan_name_en": plan.name_en if plan else "Free",
        "date": str(timezone.localdate()),
        "limits": limits,
    }


def consume(user, kind: str, ref) -> tuple[bool, dict]:
    """Kontentni "ishlatishga" urinadi. (allowed, snapshot) qaytaradi.

    Idempotent: bir xil (user, date, kind, ref) qayta chaqirilsa yana sanamaydi.
    """
    today = timezone.localdate()
    ref = str(ref)[:64]
    if DailyUsage.objects.filter(user=user, date=today, kind=kind, ref=ref).exists():
        return True, snapshot(user)

    plan = get_user_plan(user)
    lim = plan.limit_for(kind) if plan else None
    if lim is not None and used_today(user, kind) >= lim:
        return False, snapshot(user, plan)

    DailyUsage.objects.get_or_create(user=user, date=today, kind=kind, ref=ref)
    return True, snapshot(user, plan)


def notify_limit_once(user) -> None:
    """Limitga yetgan MOBIL foydalanuvchiga bot orqali bir marta (kuniga) xabar.

    **Havola ILOVADA emas, botda beriladi.** Ilovaning o'zida tashqi to'lov
    havolasi ko'rsatilsa App Store / Play Store qoidalariga tegib, publish
    qilishda muammo bo'ladi. Shu bois ilovada faqat "tarifingiz + ertaga
    yangilanadi" deb yoziladi, tarifni ko'tarish havolasi esa shu Telegram
    xabarida keladi (`/profile/billing`).

    Telegram id bo'lmasa yoki bugun allaqachon yuborilgan bo'lsa — jim o'tadi.
    """
    if not getattr(user, "telegram_id", None):
        return
    today = timezone.localdate()
    _, created = DailyUsage.objects.get_or_create(
        user=user, date=today, kind=_NOTIFY_KIND, ref="mobile",
    )
    if not created:
        return  # bugun allaqachon xabar berilgan
    try:
        from django.conf import settings
        from apps.telegrambot.models import BotMessage

        site = (getattr(settings, "SITE_URL", "") or "https://listening.uz").rstrip("/")
        plan = get_user_plan(user)
        # Bot HTML rejimida yuboradi — markdown emas, `<b>` ishlatamiz va
        # qiymatlarni escape qilamiz (`<` bo'lsa Telegram xabarni rad etadi).
        from html import escape

        plan_name = escape(
            (plan.name_en if (user.language or "uz") == "en" else plan.name_uz) if plan else "Free"
        )
        link = f"{site}/profile/billing"
        if (getattr(user, "language", "") or "uz").lower() == "en":
            text = "\n".join([
                f"Unfortunately you have reached today's limit on the <b>{plan_name}</b> plan.",
                "It resets tomorrow — or remove the limits completely by upgrading:",
                link,
            ])
        else:
            text = "\n".join([
                f"Afsuski, <b>{plan_name}</b> tarifidagi bugungi limitingizga yetdingiz.",
                "Ertaga yangilanadi — yoki boshqa tarifga o'tib limitlarni "
                "butunlay olib tashlashingiz mumkin:",
                link,
            ])
        BotMessage.objects.create(
            user=user,
            telegram_id=user.telegram_id,
            kind="system",
            text=text,
        )
    except Exception:
        # Bot yozuvi yozilmasa ham asosiy oqim buzilmasin.
        pass


def enforce_or_response(request, kind: str, ref):
    """Kontent "ko'rish" endpointlarida chaqiriladi.

    Auth foydalanuvchi uchun limitni tekshiradi + sanaydi. Limitga yetgan bo'lsa
    strukturali **403** Response qaytaradi (mobil bo'lsa bot orqali SMS ham),
    aks holda `None` — chaqiruvchi davom etaveradi. Anonim (auth yo'q) uchun
    cheklovsiz (ilovada baribir auth gate bor).
    """
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return None
    allowed, snap = consume(user, kind, ref)
    if allowed:
        return None
    if (request.headers.get("X-Platform") or "").lower() == "mobile":
        notify_limit_once(user)
    return Response(
        {
            "error": {
                "code": "limit_reached",
                "kind": kind,
                "message": "Bugungi kunlik limitingizga yetdingiz.",
            },
            "limits": snap,
        },
        status=status.HTTP_403_FORBIDDEN,
    )

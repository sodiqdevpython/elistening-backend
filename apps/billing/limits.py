"""Kunlik limit mantig'i — web va mobil bir xil shu yerdan foydalanadi.

Turlar (`kind`): `shorts`, `video`, `dictation`, `ielts`.
Limit: `None` = cheksiz, `0` = umuman mumkin emas, N = kuniga N ta noyob kontent.
"""
import hashlib

from django.core.cache import cache
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response

from .models import DailyUsage, Plan

KINDS = DailyUsage.KINDS
_NOTIFY_KIND = "_limit_notified"  # bir kunda bir marta bot SMS uchun belgisi


#: Foydalanuvchining tarifi shuncha keshlanadi (soniya).
#: Tarif kamdan-kam o'zgaradi, lekin HAR video ko'rishda o'qiladi — shu bois
#: kesh eng katta foydani shu yerda beradi. O'zgarganda darrov tozalanadi
#: (`forget_user_plan`), ya'ni TTL faqat zaxira.
PLAN_CACHE_TTL = 300


def _plan_cache_key(user_id) -> str:
    return f"user_plan_v1_{user_id}"


def forget_user_plan(user_or_id) -> None:
    """Tarif o'zgarganda keshni tozalaydi.

    `grants.grant_plan` va `billing.views.subscribe` shuni chaqiradi —
    aks holda foydalanuvchi tarifi ko'tarilgani bilan `PLAN_CACHE_TTL`
    davomida eski limitlar amal qilardi.
    """
    user_id = getattr(user_or_id, "pk", user_or_id)
    if user_id:
        cache.delete(_plan_cache_key(user_id))


def get_user_plan(user) -> Plan | None:
    """Foydalanuvchining joriy tarifi (faol obuna) yoki standart (free).

    **Keshlanadi** (`PLAN_CACHE_TTL`): bu funksiya har video ko'rishda,
    har limit tekshiruvida va profil so'rovida chaqiriladi, lekin javob
    kunlar davomida o'zgarmaydi.

    Keshda `Plan` obyekti emas, uning **id**'si saqlanadi va tariflar
    jadvali (bor-yo'g'i bir necha qator) alohida keshdan olinadi. Sabab:
    ORM obyektini pickle qilib qo'ysak, model maydonlari o'zgargan deploy'dan
    keyin kesh ochilmay xato berardi.
    """
    key = _plan_cache_key(user.pk)
    plan_id = cache.get(key)
    if plan_id is None:
        sub = user.subscriptions.select_related("plan").first()
        if sub and sub.is_active:
            plan_id = sub.plan_id
        else:
            default = _default_plan()
            plan_id = default.id if default else 0
        cache.set(key, plan_id, PLAN_CACHE_TTL)
    if not plan_id:
        return None
    return _plans_by_id().get(plan_id)


def _plans_cache_key() -> str:
    """Kalitga model MAYDONLARI ham kiradi.

    Keshda `Plan` obyektlari (pickle) yotadi. Agar keyingi deploy'da modelga
    maydon qo'shilsa, eski pickle'da u bo'lmaydi va kod `AttributeError`
    bilan yiqilardi. Maydonlar ro'yxati kalitning bir qismi bo'lgani uchun
    bunday holatda kalit O'ZGARADI — eski yozuv shunchaki e'tiborsiz qoladi
    va TTL bilan o'zi yo'qoladi. Qo'lda tozalash kerak emas.
    """
    names = ",".join(sorted(f.name for f in Plan._meta.get_fields()))
    return f"plans_by_id_v1_{hashlib.md5(names.encode()).hexdigest()[:8]}"


def _plans_by_id() -> dict[int, Plan]:
    """Barcha tariflar (jadval bir necha qator) — keshdan, so'rovsiz."""
    key = _plans_cache_key()
    plans = cache.get(key)
    if plans is None:
        plans = {p.id: p for p in Plan.objects.all()}
        cache.set(key, plans, PLAN_CACHE_TTL)
    return plans


def forget_plans() -> None:
    """Tariflar jadvali o'zgarganda (admin) keshni tozalaydi — `signals.py`."""
    cache.delete(_plans_cache_key())


def _default_plan() -> Plan | None:
    for plan in _plans_by_id().values():
        if plan.is_default:
            return plan
    for plan in _plans_by_id().values():
        if plan.code == "free":
            return plan
    return None


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


def consume(user, kind: str, ref) -> tuple[bool, Plan | None]:
    """Kontentni "ishlatishga" urinadi. `(allowed, plan)` qaytaradi.

    Idempotent: bir xil (user, date, kind, ref) qayta chaqirilsa yana sanamaydi.

    **Snapshot BU YERDA yasalmaydi.** Ilgari uchala yo'lda ham `snapshot()`
    chaqirilardi va u har turdagi limit uchun bittadan COUNT qiladi — ya'ni
    RUXSAT BERILGAN oddiy holatda ham 4 ta ortiqcha so'rov ketardi. Holbuki
    snapshot faqat 403 javobining ichida kerak. `POST /shorts/{id}/view/`
    ilovadagi eng tez-tez chaqiriladigan endpoint (har slot uchun bitta),
    shu bois bu eng qimmat isrof edi: **17 → 8 so'rov**.
    """
    today = timezone.localdate()
    ref = str(ref)[:64]
    plan = get_user_plan(user)

    if DailyUsage.objects.filter(user=user, date=today, kind=kind, ref=ref).exists():
        return True, plan

    lim = plan.limit_for(kind) if plan else None
    if lim is not None and used_today(user, kind) >= lim:
        return False, plan

    DailyUsage.objects.get_or_create(user=user, date=today, kind=kind, ref=ref)
    return True, plan


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
    allowed, plan = consume(user, kind, ref)
    if allowed:
        return None
    # Snapshot faqat SHU YERDA kerak — 403 javobining ichida.
    snap = snapshot(user, plan)
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

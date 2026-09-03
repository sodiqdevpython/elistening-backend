"""Tarif berish — BITTA joy. Hamma yo'l (to'lov, taklif, admin) shu yerdan o'tadi.

Nega alohida modul: "tarifni ko'tarish" uch joyda kerak bo'ladi (to'lov,
taklif sovg'asi, admin), va uchalasida ham bir xil xavfsizlik qoidalari amal
qilishi shart. Qoidalar bir joyda bo'lmasa, kelajakda bittasida xato ketib
foydalanuvchilar bepulga Pro bo'lib qolishi mumkin.

## Qat'iy qoidalar

1. **Hech qachon PASTGA tushirmaymiz.** Berilayotgan tarif hozirgi FAOL
   tarifdan past bo'lsa — umuman tegmaymiz va `None` qaytaramiz. Chaqiruvchi
   (masalan taklif sovg'asi) buni ko'rib takliflarni SARFLAMAYDI, ular
   keyingi safarga saqlanadi.
2. **Bir xil tarif — uzaytiriladi** (`max(now, expires_at) + N oy`), ya'ni
   qolgan kunlar yonmaydi.
3. **Yuqoriroq tarif — almashtiriladi** va muddat `now + N oy` bo'ladi.
4. **Muddatsiz obunani hech narsa qisqartirmaydi** (`expires_at is None`).

Har o'zgarish `SubscriptionEvent` ga yoziladi (signals.py) — profil
sahifasidagi tarix va Telegram xabari o'sha yozuvdan chiqadi.
"""
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import Plan, Reason, Subscription

# Bir "oy" — 30 kun. Kalendar oyini ishlatmaymiz: 31-yanvarga obuna berilsa
# 31-fevral yo'qligi sabab chetki holatlar chiqadi, foyda esa yo'q.
MONTH = timedelta(days=30)

# Tarif "kuchi" — qaysi biri yuqori ekanini shundan bilamiz.
# Narxga tayanmaymiz: dev bazada narxlar 0 bo'lishi mumkin va u holda Plus
# bilan Pro teng bo'lib qolardi (Pro'ni Plus bilan almashtirish xavfi).
_CODE_RANK = {"free": 0, "basic": 1, "start": 1, "plus": 2, "pro": 3, "premium": 4}


def plan_rank(plan: Plan | None) -> int:
    """Tarif darajasi (katta = kuchli). Noma'lum kod narxga qarab baholanadi."""
    if plan is None:
        return 0
    known = _CODE_RANK.get(plan.code)
    if known is not None:
        return known
    if plan.is_default:
        return 0
    return 2 if plan.price_uzs or plan.price_usd else 1


def current_subscription(user) -> Subscription | None:
    """Foydalanuvchining joriy obunasi (bo'lmasa None). Muddati tekshirilmaydi."""
    return Subscription.objects.select_related("plan").filter(user=user).first()


def grant_plan(user, plan: Plan, months: int, reason: str, note: str = ""):
    """Tarifni beradi/uzaytiradi. Muvaffaqiyatda `SubscriptionEvent`, aks holda None.

    `months=0` — muddatsiz (admin/test uchun). Boshqa hollarda kamida 1 oy.
    """
    if plan is None:
        return None

    unlimited = int(months) <= 0
    months = 0 if unlimited else int(months)
    now = timezone.now()

    with transaction.atomic():
        sub = Subscription.objects.select_for_update().filter(user=user).first()
        active_plan = sub.plan if (sub and sub.is_active) else None

        # 1-qoida — pastga tushirmaymiz.
        if plan_rank(plan) < plan_rank(active_plan):
            return None

        same_plan = active_plan is not None and active_plan.pk == plan.pk

        if unlimited:
            expires = None
        elif same_plan and sub.expires_at is None:
            expires = None  # 4-qoida: muddatsizni qisqartirmaymiz
        elif same_plan and sub.expires_at:
            expires = max(now, sub.expires_at) + MONTH * months  # 2-qoida
        else:
            expires = now + MONTH * months  # 3-qoida

        if sub is None:
            sub = Subscription(user=user, started_at=now)
        elif not same_plan:
            sub.started_at = now

        sub.plan = plan
        sub.status = Subscription.Status.ACTIVE
        sub.expires_at = expires
        sub.reason = reason
        # Signal (`signals.py`) shu ikkisini o'qib tarix yozuvini to'ldiradi.
        sub._grant_months = months
        sub._grant_note = note
        sub.save()

    # Tarif o'zgardi — limit keshini darrov tozalaymiz, aks holda
    # foydalanuvchi yangi tarifni oldi, lekin eski limitlar amal qilardi.
    from .limits import forget_user_plan

    forget_user_plan(user)

    # Signal `post_save` da tarix yozuvini `sub._event` ga qo'yib ketadi.
    return getattr(sub, "_event", None)


def grant_by_code(user, plan_code: str, months: int, reason: str = Reason.MANUAL, note: str = ""):
    """`grant_plan` ning kod bo'yicha qulay varianti (tarif topilmasa None)."""
    plan = Plan.objects.filter(code=plan_code, is_active=True).first()
    if plan is None:
        return None
    return grant_plan(user, plan, months, reason, note)

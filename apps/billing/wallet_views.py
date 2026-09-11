"""Hamyon REST API'si — sayt va ilova uchun (JWT).

To'lov PROVAYDERLARI bu yerga kelmaydi; ular o'z ilovalarida:

* `apps/paynet/views.py`  — Paynet JSON-RPC (Basic auth + IP ro'yxati)
* `apps/click/views.py`   — Click Prepare/Complete (MD5 imzo)

## Ikki provayder, ikki BUTUNLAY boshqa oqim

    Click (redirect):   [To'lash] → my.click.uz → karta → qaytadi
                        Click bizga Prepare/Complete yuboradi → tarif yoqiladi

    Paynet (teskari):   biz faqat "To'lov ID" ni ko'rsatamiz
                        foydalanuvchi Paynet ilovasi/kassasida O'ZI to'laydi
                        Paynet bizga JSON-RPC yuboradi → tarif yoqiladi

Shu bois mijoz interfeysi ham ikki xil: Click uchun TUGMA, Paynet uchun
YO'RIQNOMA. Ikkalasi ham oxirida bitta `Wallet` ga tushadi.
"""
from django.conf import settings
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import Plan, Wallet
from .pricing import blocking_plan, price_for
from .wallet import buy_plan

#: Bitta so'rovda 12 oydan ortiq obuna sotib olinmaydi (xato kiritishdan himoya).
MAX_MONTHS = 12


def plan_payload(user, plan: Plan, months: int, balance_tiyin: int) -> dict:
    """Mijozga ko'rsatiladigan narx — KO'TARILISH chegirmasi hisobga olingan."""
    full_uzs = int(plan.price_uzs) * months
    price_uzs = price_for(user, plan, months)
    price_tiyin = price_uzs * 100
    missing = max(0, price_tiyin - balance_tiyin)
    return {
        "plan": plan.code,
        "plan_name": plan.status_name,
        "months": months,
        # HAQIQATDA to'lanadigan summa (ko'tarilishda farq).
        "price_uzs": price_uzs,
        # Tarifning e'lon qilingan narxi — mijoz ikkalasini ko'rsata oladi.
        "full_price_uzs": full_uzs,
        "upgrade_credit_uzs": full_uzs - price_uzs,
        "missing_uzs": missing // 100,
        "enough": missing == 0,
    }


def wallet_payload(user, wallet: Wallet) -> dict:
    return {
        "balance_tiyin": wallet.balance_tiyin,
        "balance_uzs": wallet.balance_uzs,
        "balance_label": wallet.balance_label,
        "pending": (
            plan_payload(user, wallet.pending_plan, wallet.pending_months, wallet.balance_tiyin)
            if wallet.pending_plan else None
        ),
        # ── Provayderlar ────────────────────────────────────────────────
        "providers": {
            # Click — tugma bosiladi, boshqa hech narsa kerak emas.
            # `merchant_id` shart emas — `apps/click/links.py` izohiga qarang.
            "click": {
                "enabled": bool(settings.CLICK_SERVICE_ID and settings.CLICK_SECRET_KEY),
                # Mijoz tugmada "qancha to'lanadi" ni ko'rsatishi uchun:
                # yetmayotgan qism shundan kichik bo'lsa, shu summa olinadi.
                "min_uzs": int(settings.CLICK_MIN_AMOUNT_UZS),
            },
            # Paynet — foydalanuvchi kassada AYNAN shu raqamni kiritadi.
            # `telegram_id` bo'lmasa (admin qo'lda yaratgan akkaunt) to'lov
            # qilib bo'lmaydi; interfeys shuni ochiq aytishi kerak, jim qolmasligi.
            "paynet": {
                "payment_id": str(user.telegram_id) if user.telegram_id else None,
                "service_name": settings.PAYNET_SERVICE_NAME,
            },
        },
    }


def read_plan(data, user=None):
    """So'rovdagi `plan` + `months` ni tekshiradi → `(plan, months, error)`.

    `apps/click/api.py` ham shuni ishlatadi — tekshiruv ikki joyda
    ajralib ketmasin.

    `user` berilsa PASTGA TUSHIRISH ham to'xtatiladi: `grant_plan` baribir
    rad etadi, lekin uni faqat o'sha yerda ushlasak foydalanuvchi past
    tarifni tanlab, pul to'lab, keyin "nega yoqilmadi?" deb qolardi.
    """
    plan = Plan.objects.filter(code=data.get("plan"), is_active=True).first()
    if plan is None:
        return None, 1, Response({"detail": "Tarif topilmadi"}, status=status.HTTP_404_NOT_FOUND)
    if plan.is_default or not plan.price_uzs:
        return None, 1, Response(
            {"detail": "Bu tarif to'lovsiz — /api/billing/subscribe/ dan foydalaning"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        months = int(data.get("months") or 1)
    except (TypeError, ValueError):
        months = 0
    if not 1 <= months <= MAX_MONTHS:
        return None, 1, Response({"detail": f"Oy 1 dan {MAX_MONTHS} gacha bo'lishi kerak"},
                                 status=status.HTTP_400_BAD_REQUEST)

    if user is not None:
        blocker = blocking_plan(user, plan)
        if blocker is not None:
            return None, 1, Response(
                {"detail": f"Sizda «{blocker.status_name}» tarifi faol — "
                           "pastroq tarifga o'tib bo'lmaydi.",
                 "blocked_by": blocker.code},
                status=status.HTTP_400_BAD_REQUEST,
            )
    return plan, months, None


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def wallet(request):
    obj, _ = Wallet.objects.select_related("pending_plan").get_or_create(user=request.user)
    return Response(wallet_payload(request.user, obj))


def start_purchase(user, data) -> Response:
    """Tarif tanlashni boshlaydi — `intent` va `billing.subscribe` SHUNI chaqiradi.

    View emas, oddiy funksiya: `billing.subscribe` uni to'g'ridan-to'g'ri
    chaqira olishi kerak. Bir view'ni boshqasidan chaqirsak DRF so'rov
    tanasini ikkinchi marta o'qishga urinardi.
    """
    plan, months, error = read_plan(data, user)
    if error is not None:
        return error

    obj, _ = Wallet.objects.get_or_create(user=user)
    # Balans allaqachon yetarli bo'lsa DARROV yoqamiz — foydalanuvchini
    # keraksiz to'lovga yubormaymiz.
    event, obj = buy_plan(user, plan, months)
    if event is None:
        obj.pending_plan = plan
        obj.pending_months = months
        obj.save(update_fields=["pending_plan", "pending_months", "updated_at"])

    payload = wallet_payload(user, obj)
    payload["activated"] = event is not None
    payload["selected"] = plan_payload(user, plan, months, obj.balance_tiyin)
    return Response(payload)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def intent(request):
    """Tarifni tanlaydi (Paynet oqimi uchun niyat + Click uchun tanlov)."""
    return start_purchase(request.user, request.data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def buy(request):
    """Balansdagi puldan tarifni yoqadi (pul yetmasa — 402)."""
    plan, months, error = read_plan(request.data, request.user)
    if error is not None:
        return error

    event, obj = buy_plan(request.user, plan, months)
    payload = wallet_payload(request.user, obj)
    if event is None:
        payload["detail"] = "Balans yetarli emas yoki tarif joriysidan past"
        # 402 Payment Required — mijoz buni "pul to'lang" ekraniga aylantiradi.
        return Response(payload, status=status.HTTP_402_PAYMENT_REQUIRED)

    payload["activated"] = True
    payload["expires_at"] = event.expires_at
    return Response(payload)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def cancel_intent(request):
    """Tanlangan tarifdan voz kechish — keyingi to'lov balansda qoladi."""
    obj, _ = Wallet.objects.get_or_create(user=request.user)
    obj.pending_plan = None
    obj.pending_months = 1
    obj.save(update_fields=["pending_plan", "pending_months", "updated_at"])
    return Response(wallet_payload(request.user, obj))

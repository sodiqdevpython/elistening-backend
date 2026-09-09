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
from .wallet import buy_plan

#: Bitta so'rovda 12 oydan ortiq obuna sotib olinmaydi (xato kiritishdan himoya).
MAX_MONTHS = 12


def plan_payload(plan: Plan, months: int, balance_tiyin: int) -> dict:
    price_tiyin = int(plan.price_uzs) * 100 * months
    missing = max(0, price_tiyin - balance_tiyin)
    return {
        "plan": plan.code,
        "plan_name": plan.status_name,
        "months": months,
        "price_uzs": price_tiyin // 100,
        "missing_uzs": missing // 100,
        "enough": missing == 0,
    }


def wallet_payload(user, wallet: Wallet) -> dict:
    return {
        "balance_tiyin": wallet.balance_tiyin,
        "balance_uzs": wallet.balance_uzs,
        "balance_label": wallet.balance_label,
        "pending": (
            plan_payload(wallet.pending_plan, wallet.pending_months, wallet.balance_tiyin)
            if wallet.pending_plan else None
        ),
        # ── Provayderlar ────────────────────────────────────────────────
        "providers": {
            # Click — tugma bosiladi, boshqa hech narsa kerak emas.
            "click": {"enabled": bool(settings.CLICK_SERVICE_ID and settings.CLICK_MERCHANT_ID)},
            # Paynet — foydalanuvchi kassada AYNAN shu raqamni kiritadi.
            # `telegram_id` bo'lmasa (admin qo'lda yaratgan akkaunt) to'lov
            # qilib bo'lmaydi; interfeys shuni ochiq aytishi kerak, jim qolmasligi.
            "paynet": {
                "payment_id": str(user.telegram_id) if user.telegram_id else None,
                "service_name": settings.PAYNET_SERVICE_NAME,
            },
        },
    }


def read_plan(data):
    """So'rovdagi `plan` + `months` ni tekshiradi → `(plan, months, error)`.

    `apps/click/api.py` ham shuni ishlatadi — tekshiruv ikki joyda
    ajralib ketmasin.
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
    plan, months, error = read_plan(data)
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
    payload["selected"] = plan_payload(plan, months, obj.balance_tiyin)
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
    plan, months, error = read_plan(request.data)
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

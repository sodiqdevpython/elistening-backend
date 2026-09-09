"""Click SHOP-API — `Prepare` va `Complete` endpointlari.

Bu ikkisini Click'ning O'ZI chaqiradi (merchant.click.uz da "Сервисы" →
backend/callback URL sifatida yoziladi). Foydalanuvchi bu yerga hech qachon
kelmaydi.

## Oqim

    1. Sayt: buyurtma yaratiladi → ClickOrder(input), pk = merchant_trans_id
    2. Foydalanuvchi my.click.uz da to'laydi
    3. Click → POST /api/click/prepare/   (action=0) → tekshiramiz, waiting
    4. Click → POST /api/click/complete/  (action=1) → pul hamyonga, tarif yoqiladi

## Uch qat'iy qoida

1. **Imzo — birinchi.** `sign_string` noto'g'ri bo'lsa boshqa hech narsa
   qilinmaydi (`-1`). Buyurtma bor-yo'qligini ham aytmaymiz.
2. **HTTP holati DOIM 200.** Click javob TANASIDAGI `error` ni o'qiydi;
   4xx/5xx qaytarsak u "servis ishlamayapti" deb tranzaksiyani osiltiradi.
3. **Idempotentlik.** Takroriy `Complete` pulni ikki marta yozmaydi:
   `CONFIRMED` holat YAKUNIY va `-4 Already paid` qaytariladi.

## Nega DRF emas

Click `application/x-www-form-urlencoded` yuboradi va oddiy JSON kutadi.
DRF'ning content negotiation, autentikatsiya va throttle qatlamlari bu
yerda ortiqcha ish — ular faqat kechikish qo'shadi.
"""
import logging

from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from apps.billing.wallet import buy_plan, credit, try_pending_plan

from . import errors, signature
from .models import ClickOrder

log = logging.getLogger("click")

#: `Prepare` va `Complete` da bir xil bo'lishi shart bo'lgan maydonlar.
REQUIRED = (
    "click_trans_id", "service_id", "click_paydoc_id", "merchant_trans_id",
    "amount", "action", "error", "error_note", "sign_time", "sign_string",
)


def _int_or_none(raw):
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _reply(code, data, order=None, confirm=False):
    """Click kutgan javob: har doim 200, ichida `error` + `error_note`."""
    payload = {
        "click_trans_id": data.get("click_trans_id"),
        "merchant_trans_id": data.get("merchant_trans_id"),
        "error": code,
        "error_note": errors.note(code),
    }
    # `merchant_prepare_id` / `merchant_confirm_id` — bizning buyurtma pk'si.
    # Xato bo'lsa 0 yuboramiz (rasmiy kutubxonadagi xulq-atvor).
    key = "merchant_confirm_id" if confirm else "merchant_prepare_id"
    payload[key] = order.pk if order is not None else 0
    return JsonResponse(payload)


def _amount_matches(order, raw):
    """Summa mos kelishini tekshiradi (1 tiyin farqigacha).

    Click summani so'mda kasr bilan yuboradi ("23000.00"). Satrni
    to'g'ridan-to'g'ri taqqoslash ishlamaydi — "23000" va "23000.00" bir xil pul.
    """
    try:
        return abs(float(raw) - float(order.amount_uzs)) < 0.01
    except (TypeError, ValueError):
        return False


def _load(data, action):
    """Umumiy tekshiruvlar → `(order, error_code)`."""
    # 1) Majburiy maydonlar
    if any(data.get(f) is None for f in REQUIRED):
        return None, errors.BAD_REQUEST
    if action == "1" and data.get("merchant_prepare_id") is None:
        return None, errors.BAD_REQUEST

    # 2) Imzo — buyurtmani qidirishdan OLDIN.
    if not signature.is_valid(data, action):
        return None, errors.SIGN_CHECK_FAILED

    # 3) Amal
    if action not in ("0", "1"):
        return None, errors.ACTION_NOT_FOUND

    # 4) Buyurtma
    try:
        order = ClickOrder.objects.select_related("plan", "user").get(
            pk=int(data.get("merchant_trans_id")))
    except (ClickOrder.DoesNotExist, TypeError, ValueError):
        return None, errors.USER_NOT_FOUND

    # 5) Complete'da `merchant_prepare_id` biz Prepare'da bergan pk bo'lishi
    #    shart — aks holda boshqa buyurtmaning to'lovi shu yerga yozilardi.
    if action == "1" and _int_or_none(data.get("merchant_prepare_id")) != order.pk:
        return order, errors.TRANSACTION_NOT_FOUND

    if order.status == ClickOrder.Status.CONFIRMED:
        return order, errors.ALREADY_PAID
    if not _amount_matches(order, data.get("amount")):
        return order, errors.INCORRECT_AMOUNT
    if order.status == ClickOrder.Status.REJECTED:
        return order, errors.TRANSACTION_CANCELLED

    return order, errors.SUCCESS


@csrf_exempt
def prepare(request):
    """`action=0` — Click to'lovdan oldin buyurtmani tekshiradi."""
    if request.method != "POST":
        return _reply(errors.BAD_REQUEST, {})
    data = request.POST
    order, code = _load(data, str(data.get("action")))

    if code == errors.SUCCESS:
        ClickOrder.objects.filter(pk=order.pk).update(
            status=ClickOrder.Status.WAITING,
            click_trans_id=_int_or_none(data.get("click_trans_id")),
            click_paydoc_id=_int_or_none(data.get("click_paydoc_id")),
        )
    return _reply(code, data, order)


@csrf_exempt
def complete(request):
    """`action=1` — to'lov yakunlandi: pul hamyonga, tarif yoqiladi."""
    if request.method != "POST":
        return _reply(errors.BAD_REQUEST, {}, confirm=True)
    data = request.POST
    order, code = _load(data, str(data.get("action")))
    if code != errors.SUCCESS:
        return _reply(code, data, order, confirm=True)

    # Click O'ZI xato yuborishi mumkin (foydalanuvchi bekor qildi, mablag'
    # yetmadi, ...). Bunda pul KELMAGAN — buyurtmani rad etamiz va hamyonga
    # umuman tegmaymiz.
    if (_int_or_none(data.get("error")) or 0) < 0:
        ClickOrder.objects.filter(pk=order.pk).update(
            status=ClickOrder.Status.REJECTED,
            status_note=str(data.get("error_note") or "")[:200],
        )
        return _reply(errors.TRANSACTION_CANCELLED, data, order, confirm=True)

    try:
        _settle(order, data)
    except Exception:  # noqa: BLE001
        # Pul yozib bo'lmadi (baza nosozligi). Click'ga -7 qaytaramiz; u
        # so'rovni QAYTA yuboradi va buyurtma hali `waiting` bo'lgani uchun
        # ikkinchi urinish normal o'tadi.
        log.exception("Click complete failed for order %s", order.pk)
        return _reply(errors.FAILED_TO_UPDATE, data, order, confirm=True)

    return _reply(errors.SUCCESS, data, order, confirm=True)


def _settle(order, data):
    """Pulni hamyonga yozadi va buyurtmadagi tarifni yoqadi.

    Buyurtma qulf ostida qayta o'qiladi va holati YANA tekshiriladi: shu
    payt ikkinchi `Complete` kelgan bo'lsa pul ikki marta yozilmasin.
    """
    with transaction.atomic():
        locked = ClickOrder.objects.select_for_update().get(pk=order.pk)
        if locked.status == ClickOrder.Status.CONFIRMED:
            return
        locked.status = ClickOrder.Status.CONFIRMED
        locked.status_note = "Success"
        locked.click_trans_id = _int_or_none(data.get("click_trans_id")) or locked.click_trans_id
        locked.click_paydoc_id = _int_or_none(data.get("click_paydoc_id")) or locked.click_paydoc_id
        locked.paid_at = timezone.now()
        locked.save(update_fields=["status", "status_note", "click_trans_id",
                                   "click_paydoc_id", "paid_at", "updated_at"])
        credit(locked.user, locked.amount_tiyin)

    # Tarif yoqish tranzaksiyadan TASHQARIDA: u o'z atomic blokini ochadi va
    # ichida signal (tarix + Telegram xabari navbati) ishlaydi.
    event, _ = buy_plan(locked.user, locked.plan, locked.months)
    if event is None:
        # Tarif yoqilmadi (masalan joriysi yuqoriroq) — pul balansda qoladi.
        # Niyat bo'lsa uni sinab ko'ramiz.
        try_pending_plan(locked.user)

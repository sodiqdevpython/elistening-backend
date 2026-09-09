"""Hamyon amallari — pulni yozish, yechish va tarifga aylantirish.

**Provayderdan mustaqil.** Paynet ham (`apps/paynet/service.py`), Click ham
(`apps/click/views.py`) shu funksiyalarni chaqiradi. Pul bilan bog'liq
qoidalar ikki joyda takrorlanmasin — aks holda bir provayderda tuzatilgan
xato ikkinchisida qolib ketardi.

Barcha o'zgarish `select_for_update` ostida: bir vaqtda kelgan to'lov va
bekor qilish bir-birining yozuvini bosib ketmasin.
"""
from django.db import transaction

from .grants import grant_plan, plan_rank
from .models import Plan, Reason, Wallet


def get_wallet(user) -> Wallet:
    wallet, _ = Wallet.objects.get_or_create(user=user)
    return wallet


def credit(user, amount_tiyin: int) -> Wallet:
    """Hamyonga pul qo'shadi va YANGI holatni qaytaradi."""
    with transaction.atomic():
        wallet = Wallet.objects.select_for_update().get_or_create(user=user)[0]
        wallet.balance_tiyin += int(amount_tiyin)
        wallet.save(update_fields=["balance_tiyin", "updated_at"])
    return wallet


def buy_plan(user, plan: Plan, months: int = 1):
    """Balansdan pul yechib tarifni yoqadi → `(event, wallet)`.

    Pul FAQAT tarif chindan berilganda yechiladi: `grant_plan` pastga
    tushirishni rad etsa (`None` qaytarsa) balans tegilmay qoladi.
    """
    months = max(1, int(months or 1))
    price = int(plan.price_uzs) * 100 * months

    with transaction.atomic():
        wallet = Wallet.objects.select_for_update().get_or_create(user=user)[0]
        if price and wallet.balance_tiyin < price:
            return None, wallet
        event = grant_plan(user, plan, months, Reason.PAID, note="To'lov balansidan")
        if event is None:
            return None, wallet
        wallet.balance_tiyin -= price
        # Niyat bajarildi — tozalaymiz, aks holda keyingi to'lov yana
        # o'sha tarifni yoqib yuborardi.
        wallet.pending_plan = None
        wallet.pending_months = 1
        wallet.save(update_fields=["balance_tiyin", "pending_plan", "pending_months", "updated_at"])
    return event, wallet


def try_pending_plan(user) -> None:
    """To'lovdan keyin: saytda tanlangan tarif bor va pul yetsa — darrov yoqamiz.

    Xatolik BOSILADI: tarif yoqilmasa ham to'lov muvaffaqiyatli qoladi va pul
    balansda turadi (foydalanuvchi saytdan o'zi yoqadi). Aks holda tarif
    mantig'idagi kichik nosozlik provayderga "to'lov o'tmadi" deb
    qaytarilib, kassada yoki Click ekranida asossiz xato chiqardi.
    """
    try:
        wallet = Wallet.objects.select_related("pending_plan").filter(user=user).first()
        if wallet is None or wallet.pending_plan is None:
            return
        plan = wallet.pending_plan
        # Niyatdagi tarif hozirgisidan past bo'lsa `grant_plan` baribir rad
        # etadi — behuda qulf olmaymiz.
        if plan.is_default or plan_rank(plan) == 0:
            return
        buy_plan(user, plan, wallet.pending_months)
    except Exception:  # noqa: BLE001 — to'lov hech qachon bunga bog'liq emas
        pass

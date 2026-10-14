"""Hamyon amallari — pulni yozish, yechish va tarifga aylantirish.

**Provayderdan mustaqil.** Paynet ham (`apps/paynet/service.py`), Click ham
(`apps/click/views.py`), Telegram bot ham (tarif tugmalari) shu funksiyalarni
chaqiradi. Pul bilan bog'liq qoidalar ikki joyda takrorlanmasin — aks holda
bir provayderda tuzatilgan xato ikkinchisida qolib ketardi.

Barcha o'zgarish `select_for_update` ostida: bir vaqtda kelgan to'lov va
bekor qilish bir-birining yozuvini bosib ketmasin. Har o'zgarish AYNI qulf
ostida `WalletTransaction` (tarix) qatorini ham yozadi — `record()`.

**DRF import qilinmaydi** — bu modulni bot jarayoni ham ishlatadi.
"""
from django.db import transaction

from .grants import grant_plan, plan_rank
from .models import Plan, Reason, Wallet, WalletTransaction
from .pricing import price_for


class StaleBalance(Exception):
    """Balans tugma chizilgan paytdagidan farq qiladi.

    Botdagi "Tasdiqlash" ikki marta bosilsa (yoki shu orada pul tushsa)
    ikkinchi bosish shu xatoga uchraydi — tarif ikki marta sotib olinmaydi.
    """


def get_wallet(user) -> Wallet:
    wallet, _ = Wallet.objects.get_or_create(user=user)
    return wallet


def record(wallet: Wallet, kind: str, provider: str, amount_tiyin: int, **extra) -> WalletTransaction:
    """Tarix qatori. FAQAT balans o'zgargan qulf ichidan chaqiriladi —
    `balance_after_tiyin` shu paytdagi (yangi) balans."""
    return WalletTransaction.objects.create(
        user_id=wallet.user_id,
        kind=kind,
        provider=provider,
        amount_tiyin=int(amount_tiyin),
        balance_after_tiyin=wallet.balance_tiyin,
        **extra,
    )


def notify_deposit(entry: WalletTransaction) -> None:
    """Pul tushgani haqida bot xabarini navbatga qo'yadi (commit'dan keyin).

    Xabarning O'ZI (tariflar rasmi, balans, tanlash tugmalari) bot uni
    yuborayotgan paytda yig'iladi — `apps/billing/offer.py`. Shunda u eng
    yangi holatni ko'rsatadi: masalan oldindan tanlangan tarif shu to'lov
    bilan avtomatik yoqilgan bo'lsa, rasmda u allaqachon "JORIY".

    Bu yerda tarmoq chaqiruvi YO'Q — Paynet 500 ms byudjeti buzilmaydi.
    """
    user = entry.user
    telegram_id = getattr(user, "telegram_id", None)
    if not telegram_id:
        return
    from .offer import deposit_fallback_text

    from apps.telegrambot.lang import bot_lang

    text = deposit_fallback_text(entry, bot_lang(user))

    def _queue():
        from apps.telegrambot.models import BotMessage

        BotMessage.objects.create(
            user=user, telegram_id=telegram_id, kind=BotMessage.Kind.DEPOSIT,
            text=text, payload={"entry_id": entry.pk},
        )

    # Pul CHINDAN yozilgach — rollback bo'lsa xabar ham ketmaydi.
    transaction.on_commit(_queue)


def credit(user, amount_tiyin: int, provider: str, *, external_id: str = "",
           note: str = "", notify: bool = True) -> WalletTransaction:
    """Hamyonga pul qo'shadi → tarix yozuvi (unda yangi balans)."""
    with transaction.atomic():
        wallet = Wallet.objects.select_for_update().get_or_create(user=user)[0]
        wallet.balance_tiyin += int(amount_tiyin)
        wallet.save(update_fields=["balance_tiyin", "updated_at"])
        entry = record(wallet, WalletTransaction.Kind.DEPOSIT, provider, amount_tiyin,
                       external_id=str(external_id), note=note)
        if notify:
            notify_deposit(entry)
    return entry


def buy_plan(user, plan: Plan, months: int = 1, *, expect_balance_tiyin: int | None = None):
    """Balansdan pul yechib tarifni yoqadi → `(event, wallet)`.

    Pul FAQAT tarif chindan berilganda yechiladi: `grant_plan` pastga
    tushirishni rad etsa (`None` qaytarsa) balans tegilmay qoladi.

    `expect_balance_tiyin` berilsa va balans undan farq qilsa — `StaleBalance`
    (bot tugmasi eskirgan yoki ikki marta bosilgan).
    """
    months = max(1, int(months or 1))
    # To'liq narx EMAS: ko'tarilishda joriy tarif narxi chegirma bo'lib
    # ketadi (`pricing.price_for`). Narx bitta joyda hisoblanadi, aks holda
    # tugmadagi raqam bilan yechilgan summa farq qilardi.
    price = price_for(user, plan, months) * 100

    with transaction.atomic():
        wallet = Wallet.objects.select_for_update().get_or_create(user=user)[0]
        if expect_balance_tiyin is not None and wallet.balance_tiyin != int(expect_balance_tiyin):
            raise StaleBalance()
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
        if price:
            record(wallet, WalletTransaction.Kind.PURCHASE, WalletTransaction.Provider.WALLET,
                   -price, plan=plan, months=months)
    return event, wallet


def choose_plan(user, plan: Plan, months: int = 1, *, expect_balance_tiyin: int | None = None):
    """Tarif tanlash: pul yetsa DARROV yoqiladi, yetmasa NIYAT bo'lib qoladi.

    → `(status, event, wallet)`, status: `activated` | `pending` | `stale`.

    Sayt (`wallet_views.start_purchase`) va bot tugmalari shu bitta
    funksiyadan o'tadi — "botda bitta xulq, saytda boshqa" bo'lmasin.
    Pastroq tarifni chaqiruvchi OLDIN to'xtatadi (`pricing.blocking_plan`).
    """
    months = max(1, int(months or 1))
    try:
        event, wallet = buy_plan(user, plan, months, expect_balance_tiyin=expect_balance_tiyin)
    except StaleBalance:
        return "stale", None, get_wallet(user)
    if event is not None:
        return "activated", event, wallet
    wallet.pending_plan = plan
    wallet.pending_months = months
    wallet.save(update_fields=["pending_plan", "pending_months", "updated_at"])
    return "pending", None, wallet


def try_pending_plan(user):
    """To'lovdan keyin: saytda tanlangan tarif bor va pul yetsa — darrov yoqamiz.

    Yoqilgan bo'lsa `SubscriptionEvent`, aks holda `None`.

    Xatolik BOSILADI: tarif yoqilmasa ham to'lov muvaffaqiyatli qoladi va pul
    balansda turadi (foydalanuvchi saytdan o'zi yoqadi). Aks holda tarif
    mantig'idagi kichik nosozlik provayderga "to'lov o'tmadi" deb
    qaytarilib, kassada yoki Click ekranida asossiz xato chiqardi.
    """
    try:
        wallet = Wallet.objects.select_related("pending_plan").filter(user=user).first()
        if wallet is None or wallet.pending_plan is None:
            return None
        plan = wallet.pending_plan
        # Niyatdagi tarif hozirgisidan past bo'lsa `grant_plan` baribir rad
        # etadi — behuda qulf olmaymiz.
        if plan.is_default or plan_rank(plan) == 0:
            return None
        event, _ = buy_plan(user, plan, wallet.pending_months)
        return event
    except Exception:  # noqa: BLE001 — to'lov hech qachon bunga bog'liq emas
        return None

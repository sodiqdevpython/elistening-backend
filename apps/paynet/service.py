"""Paynet metodlarining BIZNES mantiqi.

`rpc.py` faqat JSON-RPC konvertini ochadi/yopadi; pul bilan bog'liq hamma
qaror shu faylda. Shu bois testlar ham HTTP'siz, to'g'ridan-to'g'ri shu
funksiyalarni chaqira oladi.

## Uch qat'iy qoida

1. **Idempotentlik.** `transaction_id` UNIQUE. Paynet javobni olmay qolsa
   so'rovni qaytaradi — pul ikki marta yozilmaydi, takroriy so'rovga
   201 ("Транзакция уже существует") qaytadi (Paynet talabi, 11.09.2026).
2. **Balans faqat qulf ostida.** Har o'zgarish `select_for_update` ichida.
   `PerformTransaction` va `CancelTransaction` bir vaqtda kelsa ham balans
   buzilmaydi.
3. **500 ms.** Приложение №2, 4.5-band. Shu bois bu yerda tarmoq chaqiruvi
   YO'Q: Telegram xabari `BotMessage` navbatiga yoziladi (bot uni o'zi olib
   jo'natadi), tashqi HTTP umuman yo'q.
"""
from datetime import datetime

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.billing.models import Wallet
from apps.billing.wallet import get_wallet, try_pending_plan

from . import errors, security
from .models import PaynetTransaction

#: Paynet standart formati (UWS_JSON 2.2). Ikkinchisi — hujjatning O'ZIDAGI
#: nomuvofiqlik: `CancelTransaction` misolida "16.06.2021 12:44:57" turibdi.
#: Ikkalasini ham qabul qilamiz, aks holda bekor qilish so'rovi 414 bilan
#: rad etilib, Paynet tomonda "osilgan" tranzaksiya qolardi.
DATE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%d.%m.%Y %H:%M:%S")


# ── Yordamchilar ──────────────────────────────────────────────────────────
def now_str() -> str:
    """Joriy vaqt GMT+5 da, Paynet formatida.

    `TIME_ZONE = "Asia/Tashkent"` (UTC+5), shu bois `localtime` aynan
    hujjat talab qilgan GMT+5 ni beradi.
    """
    return timezone.localtime().strftime("%Y-%m-%d %H:%M:%S")


def fmt(moment) -> str:
    return timezone.localtime(moment).strftime("%Y-%m-%d %H:%M:%S")


def parse_dt(raw, field: str = "timestamp"):
    """Paynet bergan sanani aware `datetime` ga aylantiradi (GMT+5 deb o'qib)."""
    if not isinstance(raw, str) or not raw.strip():
        raise errors.PaynetError(errors.BAD_DATETIME, f"Bad {field}")
    for pattern in DATE_FORMATS:
        try:
            naive = datetime.strptime(raw.strip(), pattern)
        except ValueError:
            continue
        return timezone.make_aware(naive, timezone.get_current_timezone())
    raise errors.PaynetError(errors.BAD_DATETIME, f"Bad {field}")


def resolve_user(fields):
    """`fields.client_id` (Telegram chat ID) bo'yicha foydalanuvchini topadi.

    Paynet kassasi to'lovdan OLDIN `GetInformation` chaqiradi, shu bois
    ro'yxatdan o'tmagan ID bilan to'lov deyarli hech qachon kelmaydi. Baribir
    tekshiramiz: kelib qolsa 302 ("Клиент не найден") bilan rad etamiz —
    egasiz pulni keyin qaytarish ancha og'ir ish.
    """
    from apps.accounts.models import User

    if not isinstance(fields, dict):
        raise errors.PaynetError(errors.MISSING_PARAMS)
    raw = fields.get(settings.PAYNET_CLIENT_FIELD)
    if raw is None or str(raw).strip() == "":
        raise errors.PaynetError(errors.MISSING_PARAMS)
    try:
        telegram_id = int(str(raw).strip())
    except ValueError:
        # 401 = "1-parametr validatsiyasi" — kassada aynan shu maydon
        # qizarib, agent xatoni darrov ko'radi.
        raise errors.PaynetError(errors.INVALID_PARAM_1)

    user = User.objects.filter(telegram_id=telegram_id).first()
    if user is None:
        raise errors.PaynetError(errors.CLIENT_NOT_FOUND)
    if not user.is_active:
        raise errors.PaynetError(errors.TRN_FORBIDDEN)
    return user


def balance_value(wallet) -> int:
    """Paynet'ga qaytariladigan balans — **SO'MDA**.

    Diqqat: `amount` teskari yo'nalishda TIYINDA keladi va Приложение №2
    Таблица 4 ham balansni "тийин" deb belgilagan. Ammo Paynet integratsiya
    tekshiruvida (11.09.2026) balansni **so'mda** qaytarish talab qilindi —
    kassa ekranida odam o'qiydigan raqam turishi kerak.

    Birlikni qaytarib o'zgartirish kerak bo'lsa — FAQAT shu funksiya:
    `GetInformation` ham, `PerformTransaction` ham shundan oladi, ya'ni
    ikkalasi hech qachon ajralib ketmaydi.
    """
    return wallet.balance_tiyin // 100


def payer_name(user) -> str:
    """Kassa ekrani va chekda chiqadigan ism.

    Avtomatik `tg<chat_id>` username KO'RSATILMAYDI — u foydalanuvchi
    tanlagan nom emas (`frontend/src/utils/username.ts` bilan bir xil qoida).
    Ism ham, haqiqiy username ham bo'lmasa — Telegram ID, chunki kassir
    aynan shu raqamni kiritgan va uni tanib oladi.
    """
    import re

    if (user.display_name or "").strip():
        return user.display_name.strip()
    username = (user.username or "").strip()
    if username and not re.fullmatch(r"tg\d+", username):
        return username
    return f"ID {user.telegram_id}"


def check_amount(raw) -> int:
    """Summani tekshiradi (tiyinda) — chegaralar `settings` dan."""
    # `bool` — `int` ning avlodi: `True` 1 tiyinga aylanib ketmasin.
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        raise errors.PaynetError(errors.BAD_AMOUNT)
    try:
        exact = float(raw)
        amount = int(exact)
    except (TypeError, ValueError):
        raise errors.PaynetError(errors.BAD_AMOUNT)
    # Tiyin — bo'linmas birlik: 100.5 tiyin degan summa yo'q. Kasrni jimgina
    # yaxlitlasak sverkada har safar tiyin farqi chiqardi.
    if amount <= 0 or amount != exact:
        raise errors.PaynetError(errors.BAD_AMOUNT)
    if amount < settings.PAYNET_MIN_AMOUNT_TIYIN:
        raise errors.PaynetError(errors.BAD_AMOUNT)
    if amount > settings.PAYNET_MAX_AMOUNT_TIYIN:
        raise errors.PaynetError(errors.AMOUNT_TOO_BIG)
    return amount


def check_transaction_id(raw) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise errors.PaynetError(errors.MISSING_PARAMS, "Bad transactionId")


# ── Paynet metodlari ──────────────────────────────────────────────────────
def get_information(params: dict) -> dict:
    """Kassa to'lovdan OLDIN chaqiradi — mijoz bormi, ismi va balansi qanday."""
    security.service_allowed(params.get("serviceId"))
    user = resolve_user(params.get("fields"))
    wallet = get_wallet(user)

    return {
        # SATR, son emas. Spetsifikatsiya jadvali `Number` deydi, ammo
        # 3.1-dagi MISOL `"status": "0"` ko'rsatadi va Paynet integratsiya
        # tekshiruvida aynan satr talab qilindi (11.09.2026).
        "status": str(errors.OK),
        "timestamp": now_str(),
        "fields": {
            # Kassir chekdan oldin ismni ko'rib, to'g'ri odamga to'layotganini
            # tasdiqlaydi — noto'g'ri ID bilan to'lovlarning asosiy oldini olish.
            "name": payer_name(user),
            "balance": balance_value(wallet),
        },
    }


def perform_transaction(params: dict) -> dict:
    """To'lovni qabul qiladi va balansga yozadi."""
    service_id = security.service_allowed(params.get("serviceId"))
    trn_id = check_transaction_id(params.get("transactionId"))
    amount = check_amount(params.get("amount"))
    fields = params.get("fields") or {}
    user = resolve_user(fields)

    # ── Idempotentlik: allaqachon bormi? ──
    existing = PaynetTransaction.objects.filter(transaction_id=trn_id).first()
    if existing is not None:
        return _already_exists(existing)

    try:
        with transaction.atomic():
            wallet = Wallet.objects.select_for_update().get_or_create(user=user)[0]
            txn = PaynetTransaction.objects.create(
                transaction_id=trn_id,
                service_id=service_id,
                user=user,
                amount_tiyin=amount,
                state=PaynetTransaction.State.SUCCESS,
                fields=fields,
                performed_at=timezone.now(),
            )
            wallet.balance_tiyin += amount
            wallet.save(update_fields=["balance_tiyin", "updated_at"])
    except IntegrityError:
        # Ayni damda AYNAN shu `transactionId` bilan ikkinchi so'rov o'tib
        # ketdi (Paynet retry'i). UNIQUE indeks bizni ikki marta yozishdan
        # saqladi — oddiy takroriy so'rov kabi 201 qaytaramiz.
        existing = PaynetTransaction.objects.filter(transaction_id=trn_id).first()
        if existing is None:
            raise
        return _already_exists(existing)

    try_pending_plan(user)

    return {
        "providerTrnId": txn.provider_trn_id,
        "timestamp": fmt(txn.performed_at),
        # Maydonlar Приложение №2 Таблица 4 da AYNAN shu tarkibda e'lon
        # qilingan (`paynet_docs/universal_tech_doc_v_1_5_eListening.docx`).
        # Bu yerga maydon qo'shsangiz yoki olib tashlasangiz — hujjatni ham
        # yangilang, aks holda Paynet chekda boshqacha narsa kutadi.
        "fields": {
            settings.PAYNET_CLIENT_FIELD: str(user.telegram_id),
            "name": payer_name(user),
            # Таблица 4 — to'lovdan keyingi balans MAJBURIY. Yangi qiymatni
            # bazadan qayta o'qiymiz: `try_pending_plan` uni kamaytirgan
            # bo'lishi mumkin va chekda haqiqiy qoldiq turishi kerak.
            "balance": balance_value(get_wallet(user)),
        },
    }


def _already_exists(existing: PaynetTransaction):
    """Takroriy `PerformTransaction` — HAR DOIM **201**.

    Ilgari bu yerda "parametrlar bir xil bo'lsa eski javobni qaytaramiz"
    degan mantiq bor edi: Paynet javobni olmay qolsa so'rovni qaytaradi va
    o'sha `providerTrnId` ni kutadi deb TAXMIN qilgandik.

    Paynet integratsiya tekshiruvi (11.09.2026) buni rad etdi: bir xil
    `transactionId` bilan kelgan takroriy so'rovga **201 — Транзакция уже
    существует** qaytarilishi shart. Protokol nuqtai nazaridan bu to'g'ri:
    201 "yozuv bor" degani, ya'ni Paynet uni muvaffaqiyatsizlik emas,
    "allaqachon bajarilgan" deb o'qiydi.

    Pul baribir ikki marta yozilmaydi — buni javob kodi emas,
    `transaction_id` ustidagi UNIQUE indeks kafolatlaydi.
    """
    raise errors.PaynetError(errors.TRN_EXISTS)


def check_transaction(params: dict) -> dict:
    """Tranzaksiya holati. Topilmasa — xato emas, `transactionState: 3`."""
    security.service_allowed(params.get("serviceId"))
    trn_id = check_transaction_id(params.get("transactionId"))
    if "timestamp" in params:
        parse_dt(params.get("timestamp"))  # faqat formatni tekshiramiz

    txn = PaynetTransaction.objects.filter(transaction_id=trn_id).first()
    if txn is None:
        # 4-bo'lim: 3 — "Транзакция не найдена". Bu NORMAL javob (xato emas),
        # shu bois Paynet uni "noma'lum holat" deb emas, "yo'q" deb o'qiydi.
        return {"providerTrnId": 0, "timestamp": now_str(),
                "transactionState": PaynetTransaction.State.NOT_FOUND}
    moment = txn.cancelled_at or txn.performed_at
    return {"providerTrnId": txn.provider_trn_id, "timestamp": fmt(moment),
            "transactionState": txn.state}


def cancel_transaction(params: dict) -> dict:
    """To'lovni bekor qiladi — pulni balansdan qaytarib yechadi."""
    security.service_allowed(params.get("serviceId"))
    trn_id = check_transaction_id(params.get("transactionId"))
    if "timestamp" in params:
        parse_dt(params.get("timestamp"))

    txn = PaynetTransaction.objects.filter(transaction_id=trn_id).first()
    if txn is None:
        return {"providerTrnId": 0, "timestamp": now_str(),
                "transactionState": PaynetTransaction.State.NOT_FOUND}

    # Takroriy bekor qilish — xato emas: holat allaqachon 2, shunday deymiz.
    if txn.state == PaynetTransaction.State.CANCELLED:
        return {"providerTrnId": txn.provider_trn_id,
                "timestamp": fmt(txn.cancelled_at or txn.performed_at),
                "transactionState": PaynetTransaction.State.CANCELLED}

    with transaction.atomic():
        locked = PaynetTransaction.objects.select_for_update().get(pk=txn.pk)
        if locked.state == PaynetTransaction.State.CANCELLED:
            return {"providerTrnId": locked.provider_trn_id,
                    "timestamp": fmt(locked.cancelled_at or locked.performed_at),
                    "transactionState": PaynetTransaction.State.CANCELLED}

        wallet = Wallet.objects.select_for_update().get_or_create(user=locked.user)[0]
        if wallet.balance_tiyin < locked.amount_tiyin:
            # Pul allaqachon tarifga sarflangan. Hujjat aynan shu holat uchun
            # 77-kodni ajratgan — tarifni qaytarib olmaymiz.
            raise errors.PaynetError(errors.NOT_ENOUGH_TO_CANCEL)

        wallet.balance_tiyin -= locked.amount_tiyin
        wallet.save(update_fields=["balance_tiyin", "updated_at"])
        locked.state = PaynetTransaction.State.CANCELLED
        locked.cancelled_at = timezone.now()
        locked.save(update_fields=["state", "cancelled_at", "updated_at"])

    return {"providerTrnId": locked.provider_trn_id, "timestamp": fmt(locked.cancelled_at),
            "transactionState": PaynetTransaction.State.CANCELLED}


def get_statement(params: dict) -> dict:
    """Davr bo'yicha MUVAFFAQIYATLI to'lovlar — kunlik sverka uchun.

    Bekor qilinganlari kirmaydi: Paynet reestri ham qabul qilingan
    to'lovlardan iborat, ikkala tomon bir xil to'plamni solishtirishi kerak.
    """
    service_id = security.service_allowed(params.get("serviceId"))
    date_from = parse_dt(params.get("dateFrom"), "dateFrom")
    date_to = parse_dt(params.get("dateTo"), "dateTo")
    if date_from > date_to:
        raise errors.PaynetError(errors.BAD_DATETIME, "dateFrom > dateTo")

    rows = (
        PaynetTransaction.objects
        .filter(service_id=service_id, state=PaynetTransaction.State.SUCCESS,
                performed_at__gte=date_from, performed_at__lte=date_to)
        .order_by("performed_at")
        .values("amount_tiyin", "id", "transaction_id", "performed_at")
    )
    return {
        "statements": [
            {
                # Uchalasi ham ATAYLAB `int()` — Paynet `transactionId`
                # son ekanini talab qiladi, biz uni baza drayveriga
                # tashlab qo'ymaymiz.
                "amount": int(r["amount_tiyin"]),
                "providerTrnId": int(r["id"]),
                "transactionId": int(r["transaction_id"]),
                "timestamp": fmt(r["performed_at"]),
            }
            for r in rows
        ]
    }


def change_password(params: dict):
    """Web-servis parolini almashtiradi (Приложение №2 izohi bo'yicha)."""
    new_password = params.get("newPassword")
    if not isinstance(new_password, str) or len(new_password.strip()) < 8:
        raise errors.PaynetError(errors.MISSING_PARAMS, "newPassword is too short")
    security.set_password(new_password.strip())
    return "success"

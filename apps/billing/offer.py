"""Botdagi "pul tushdi" xabari va tarif tugmalari — MA'LUMOT va MATN.

Pul tushganda (Paynet ham, Click ham) foydalanuvchi botda bitta xabar oladi:
tariflar RASMI (saytdagi "Tarifni tanlang" kartochkalari bilan bir xil),
ostida qancha tushgani / balans / joriy tarif, va har tarif uchun TUGMA.
Tugma bosilsa tasdiqlash so'raladi, keyin `wallet.choose_plan` — sayt
bilan AYNAN bir xil qoida: pul yetsa darrov yoqiladi, yetmasa "niyat" bo'lib
qoladi va yetishmagan pul tushishi bilan avtomatik yoqiladi.

Bu modul faqat ma'lumot va matn yig'adi (Django ORM, DRF'siz) — tarmoq yo'q.
Rasmni bot chizadi (`bot/plan_image.py`, Pillow), tugmalarni ham bot
yasaydi; ikkalasi ham shu yerdagi lug'atlardan foydalanadi, shu bois
ularni Django testlari bilan tekshirsa bo'ladi.

Tugma `callback_data` lari (Telegram cheklovi — 64 bayt):

    pl:<kod>                 tarif tanlandi → tasdiqlash ekrani
    pk:<kod>:<balans_tiyin>  tasdiqlandi. Balans tugma chizilgandagidan
                             farq qilsa — eskirgan (ikki marta bosilgan)
    pb                       orqaga — tariflar ro'yxati
"""
from html import escape

from django.utils import timezone

from .grants import MONTH, current_subscription, plan_rank
from .models import Plan, Wallet, WalletTransaction
from .pricing import blocking_plan, price_for
from .wallet import choose_plan

CB_PLAN = "pl"
CB_OK = "pk"
CB_BACK = "pb"

#: Kartochka matnlari — `frontend/src/features/billing/PlanCards.tsx`
#: (`STATUS_PLANS`) bilan AYNAN bir xil. U yerda o'zgarsa — shu yerda ham.
#: Narx bu yerda YO'Q: u bazadan (`Plan.price_uzs`) olinadi, chunki pul
#: aynan shu raqam bo'yicha yechiladi.
PLAN_COPY = {
    "free": {
        "tagline": {"uz": "Bepul — sinab ko‘rish uchun", "en": "Free — to get started"},
        "features": {
            "uz": ["Kuniga 8 ta Shorts", "Kuniga 3 ta video", "Kuniga 5 ta diktant"],
            "en": ["8 Shorts per day", "3 videos per day", "5 dictations per day"],
        },
    },
    "plus": {
        "highlight": True,
        "tagline": {"uz": "Faol o‘rganuvchilar uchun", "en": "For active learners"},
        "features": {
            "uz": ["Kuniga 30 ta Shorts", "Kuniga 10 ta video", "Cheksiz diktant",
                   "Kuniga 2 ta IELTS test"],
            "en": ["30 Shorts per day", "10 videos per day", "Unlimited dictation",
                   "2 IELTS tests per day"],
        },
    },
    "pro": {
        "tagline": {"uz": "Cheksiz — hammasi ochiq", "en": "Unlimited — everything unlocked"},
        "features": {
            "uz": ["Cheksiz Shorts", "Cheksiz video", "Cheksiz diktant", "Cheksiz IELTS test"],
            "en": ["Unlimited Shorts", "Unlimited videos", "Unlimited dictation",
                   "Unlimited IELTS tests"],
        },
    },
}

PROVIDER_LABEL = {
    WalletTransaction.Provider.PAYNET: "Paynet",
    WalletTransaction.Provider.CLICK: "Click",
    WalletTransaction.Provider.ADMIN: "Admin",
    WalletTransaction.Provider.WALLET: "Hamyon",
}


# ── Yordamchilar ──────────────────────────────────────────────────────────
def user_lang(user) -> str:
    """Bot xabari tili — doim o'zbekcha (`apps/telegrambot/lang.py`)."""
    from apps.telegrambot.lang import bot_lang

    return bot_lang(user)


def money(uzs: int, lang: str = "uz") -> str:
    value = f"{int(uzs):,}"
    return f"{value} UZS" if lang == "en" else value.replace(",", " ") + " so‘m"


def plan_name(plan: Plan) -> str:
    """Oddiy / O‘rta / Yuqori — tizim bo'ylab bir xil (`Plan.status_name`)."""
    return plan.status_name.replace("'", "‘")


def fmt_date(moment) -> str:
    return timezone.localtime(moment).strftime("%d.%m.%Y")


def _t(lang: str, uz: str, en: str) -> str:
    return en if lang == "en" else uz


# ── Holat ─────────────────────────────────────────────────────────────────
def build_offer(user, lang: str = "uz") -> dict:
    """Foydalanuvchining HOZIRGI holati: balans, joriy tarif, har tarif narxi.

    Rasm, xabar matni va tugmalar shundan chiqadi — uchalasi bir xil
    raqamlarni ko'rsatadi.
    """
    wallet = Wallet.objects.select_related("pending_plan").get_or_create(user=user)[0]
    sub = current_subscription(user)
    active = sub if (sub is not None and sub.is_active) else None
    default = Plan.objects.filter(is_default=True, is_active=True).first()
    current = active.plan if active else default

    cards = []
    for plan in sorted(Plan.objects.filter(is_active=True, code__in=PLAN_COPY), key=plan_rank):
        copy = PLAN_COPY[plan.code]
        paid = not plan.is_default and plan.price_uzs > 0
        pay = price_for(user, plan) if paid else 0
        # Saytdagi kabi (`PlanCards`: index < currentIndex) — bepul tarif ham
        # xiralashadi. Pullik tarif uchun bu `pricing.blocking_plan` bilan bir xil.
        lower = current is not None and plan_rank(plan) < plan_rank(current)
        cards.append({
            "code": plan.code,
            "name": plan_name(plan),
            "tagline": copy["tagline"][lang],
            "features": copy["features"][lang],
            "highlight": bool(copy.get("highlight")),
            "price_uzs": int(plan.price_uzs),
            "pay_uzs": pay,
            "upgrade": paid and 0 < pay < plan.price_uzs,
            "current": current is not None and plan.pk == current.pk,
            "lower": lower,
            "buyable": paid and not lower,
            "enough": wallet.balance_tiyin >= pay * 100,
            "pending": wallet.pending_plan_id == plan.pk,
        })

    return {
        "lang": lang,
        "balance_tiyin": wallet.balance_tiyin,
        "balance_uzs": wallet.balance_uzs,
        "current": None if current is None else {
            "code": current.code,
            "name": plan_name(current),
            "expires_at": active.expires_at if active else None,
            "paid": bool(active and not current.is_default),
        },
        "plans": cards,
    }


# ── Matnlar (bot HTML rejimida — hamma qiymat escape) ─────────────────────
def _current_line(offer: dict, lang: str) -> str:
    cur = offer["current"]
    if cur is None:
        return ""
    name = escape(cur["name"])
    if cur["paid"] and cur["expires_at"]:
        until = fmt_date(cur["expires_at"])
        return _t(lang, f"⭐ Joriy tarif: <b>{name}</b> — {until} gacha",
                  f"⭐ Current plan: <b>{name}</b> — until {until}")
    return _t(lang, f"⭐ Joriy tarif: <b>{name}</b>", f"⭐ Current plan: <b>{name}</b>")


def _status_lines(offer: dict, lang: str) -> list[str]:
    """Balans, joriy tarif va qaysi tarifga pul yetishi."""
    lines = [
        _t(lang, "💰 Balans: ", "💰 Balance: ") + f"<b>{money(offer['balance_uzs'], lang)}</b>",
        _current_line(offer, lang),
    ]
    buyable = [c for c in offer["plans"] if c["buyable"]]
    pending = next((c for c in buyable if c["pending"]), None)
    if pending and not pending["enough"]:
        missing = pending["pay_uzs"] - offer["balance_uzs"]
        lines.append(_t(
            lang,
            f"📌 Tanlangan tarif: <b>{escape(pending['name'])}</b> — yana {money(missing, lang)} kerak",
            f"📌 Selected plan: <b>{escape(pending['name'])}</b> — {money(missing, lang)} more needed",
        ))

    affordable = [c for c in buyable if c["enough"]]
    lines.append("")
    if affordable:
        names = ", ".join(f"{escape(c['name'])} ({money(c['pay_uzs'], lang)})" for c in affordable)
        lines.append(_t(lang, f"Balansingiz yetadi: {names}.", f"Your balance covers: {names}."))
    elif buyable:
        nearest = min(buyable, key=lambda c: c["pay_uzs"])
        missing = nearest["pay_uzs"] - offer["balance_uzs"]
        lines.append(_t(
            lang,
            f"<b>{escape(nearest['name'])}</b> uchun yana {money(missing, lang)} kerak. "
            "Tarifni tanlab qo‘ysangiz, pul to‘lishi bilan avtomatik yoqiladi.",
            f"<b>{escape(nearest['name'])}</b> needs {money(missing, lang)} more. "
            "Pick it now and it switches on automatically once the money arrives.",
        ))
    if buyable:
        lines.append(_t(lang, "👇 Tarifni pastdagi tugma bilan tanlang.",
                        "👇 Choose a plan with the buttons below."))
    return [line for line in lines if line is not None]


def deposit_caption(offer: dict, entry: WalletTransaction, lang: str) -> str:
    provider = PROVIDER_LABEL.get(entry.provider, entry.get_provider_display())
    head = _t(
        lang,
        f"✅ Hisobingizga <b>+{money(entry.amount_uzs, lang)}</b> tushdi ({escape(provider)}).",
        f"✅ <b>+{money(entry.amount_uzs, lang)}</b> added to your balance ({escape(provider)}).",
    )
    return "\n".join([head, *_status_lines(offer, lang)])


def offer_caption(offer: dict, lang: str) -> str:
    head = _t(lang, "💳 <b>Hisob va tariflar</b>", "💳 <b>Balance and plans</b>")
    return "\n".join([head, *_status_lines(offer, lang)])


def deposit_fallback_text(entry: WalletTransaction, lang: str) -> str:
    """`BotMessage.text` — admin ro'yxatida ko'rinadi va bot holatni
    yig'a olmasa (kutilmagan xato) shu oddiy matn yuboriladi."""
    provider = PROVIDER_LABEL.get(entry.provider, entry.provider)
    balance = money(entry.balance_after_uzs or 0, lang)
    return _t(
        lang,
        f"✅ Hisobingizga +{money(entry.amount_uzs, lang)} tushdi ({provider}). Balans: {balance}.",
        f"✅ +{money(entry.amount_uzs, lang)} added to your balance ({provider}). Balance: {balance}.",
    )


# ── Tugmalar: [[{"text", "data"}], ...] — bot aiogram tugmasiga aylantiradi ──
def offer_buttons(offer: dict, lang: str) -> list[list[dict]]:
    rows = []
    for card in offer["plans"]:
        if not card["buyable"]:
            continue
        price = money(card["pay_uzs"], lang)
        if card["current"]:
            label = _t(lang, f"🔁 {card['name']} — uzaytirish · {price}",
                       f"🔁 {card['name']} — renew · {price}")
        elif card["upgrade"]:
            label = _t(lang, f"⬆️ {card['name']} · {price} (farq)", f"⬆️ {card['name']} · {price} (difference)")
        else:
            label = f"{card['name']} · {price}"
        if not card["enough"]:
            missing = money(card["pay_uzs"] - offer["balance_uzs"], lang)
            label += _t(lang, f" — yana {missing}", f" — {missing} more")
        rows.append([{"text": label, "data": f"{CB_PLAN}:{card['code']}"}])
    return rows


def _back(lang: str) -> list[dict]:
    return [{"text": _t(lang, "↩️ Tariflar", "↩️ Plans"), "data": CB_BACK}]


# ── Bot amallari (tugma bosilganda) ───────────────────────────────────────
def _plan_or_error(user, code: str, lang: str):
    plan = Plan.objects.filter(code=code, is_active=True).first()
    if plan is None or plan.is_default or not plan.price_uzs:
        return None, _t(lang, "Bu tarif topilmadi.", "This plan was not found.")
    blocker = blocking_plan(user, plan)
    if blocker is not None:
        return None, _t(
            lang,
            f"Sizda «{plan_name(blocker)}» tarifi faol — pastroq tarifga o‘tib bo‘lmaydi.",
            f"Your «{plan_name(blocker)}» plan is active — you cannot move to a lower plan.",
        )
    return plan, None


def offer_view(user) -> dict:
    """Tariflar ro'yxati (`/balans` buyrug'i va "↩️ Tariflar" tugmasi)."""
    lang = user_lang(user)
    offer = build_offer(user, lang)
    return {"text": offer_caption(offer, lang), "buttons": offer_buttons(offer, lang), "offer": offer}


def plan_prompt(user, code: str) -> dict:
    """`pl:<kod>` — tasdiqlash ekrani. Pul yechilmaydi."""
    lang = user_lang(user)
    plan, error = _plan_or_error(user, code, lang)
    if error:
        return {"alert": error}

    wallet = Wallet.objects.get_or_create(user=user)[0]
    pay = price_for(user, plan)
    name = escape(plan_name(plan))
    balance = wallet.balance_uzs
    lines = [_t(lang, f"<b>{name}</b> tarifi · 1 oy", f"<b>{name}</b> plan · 1 month")]

    sub = current_subscription(user)
    same = sub is not None and sub.is_active and sub.plan_id == plan.pk
    if same and sub.expires_at:
        new_until = max(timezone.now(), sub.expires_at) + MONTH
        lines.append(_t(
            lang,
            f"Joriy tarifingiz 1 oyga uzaytiriladi: {fmt_date(sub.expires_at)} → {fmt_date(new_until)}",
            f"Your current plan is extended by 1 month: {fmt_date(sub.expires_at)} → {fmt_date(new_until)}",
        ))
    if 0 < pay < plan.price_uzs:
        lines.append(_t(
            lang,
            f"Narxi: <b>{money(pay, lang)}</b> (to‘liq narx {money(plan.price_uzs, lang)}, "
            "joriy tarif uchun to‘langani hisobga olindi)",
            f"Price: <b>{money(pay, lang)}</b> (full price {money(plan.price_uzs, lang)}, "
            "minus what you paid for the current plan)",
        ))
    else:
        lines.append(_t(lang, f"Narxi: <b>{money(pay, lang)}</b>", f"Price: <b>{money(pay, lang)}</b>"))

    ok_data = f"{CB_OK}:{plan.code}:{wallet.balance_tiyin}"
    if wallet.balance_tiyin >= pay * 100:
        lines.append(_t(lang, f"Balans: {money(balance, lang)} → {money(balance - pay, lang)}",
                        f"Balance: {money(balance, lang)} → {money(balance - pay, lang)}"))
        lines += ["", _t(lang, "Tasdiqlaysizmi?", "Confirm?")]
        ok_text = _t(lang, "✅ Tasdiqlash", "✅ Confirm")
    else:
        missing = pay - balance
        lines.append(_t(lang, f"Balans: {money(balance, lang)} — yana <b>{money(missing, lang)}</b> kerak.",
                        f"Balance: {money(balance, lang)} — <b>{money(missing, lang)}</b> more needed."))
        lines += ["", _t(
            lang,
            "Tanlab qo‘ysangiz, yetishmagan pul tushishi bilan tarif <b>avtomatik</b> yoqiladi.",
            "Pick it now and the plan switches on <b>automatically</b> once the rest arrives.",
        )]
        if user.telegram_id:
            lines.append(_t(lang, f"Paynet uchun To‘lov ID: <code>{user.telegram_id}</code>",
                            f"Paynet payment ID: <code>{user.telegram_id}</code>"))
        ok_text = _t(lang, "📌 Tanlab qo‘yish", "📌 Pick this plan")

    return {
        "text": "\n".join(lines),
        "buttons": [[{"text": ok_text, "data": ok_data}],
                    [{"text": _t(lang, "↩️ Orqaga", "↩️ Back"), "data": CB_BACK}]],
    }


def apply_choice(user, code: str, expect_balance_tiyin: int) -> dict:
    """`pk:<kod>:<balans>` — sotib olish yoki niyat. Pul FAQAT shu yerda yechiladi."""
    lang = user_lang(user)
    plan, error = _plan_or_error(user, code, lang)
    if error:
        return {"alert": error}

    status, event, wallet = choose_plan(user, plan, 1, expect_balance_tiyin=expect_balance_tiyin)
    if status == "stale":
        # Ikki marta bosildi yoki shu orada pul tushdi — hech narsa yechilmadi,
        # yangi holatni ko'rsatamiz.
        view = offer_view(user)
        view["alert"] = _t(lang, "Balans o‘zgardi — yangilangan holat ko‘rsatildi.",
                           "Your balance changed — showing the updated state.")
        return view

    name = escape(plan_name(plan))
    if status == "activated":
        until = (_t(lang, f"{fmt_date(event.expires_at)} gacha", f"until {fmt_date(event.expires_at)}")
                 if event.expires_at else _t(lang, "muddatsiz", "no expiry"))
        text = _t(
            lang,
            f"✅ <b>{name}</b> tarifi yoqildi — {until}.\nBalans: {money(wallet.balance_uzs, lang)}",
            f"✅ <b>{name}</b> plan is on — {until}.\nBalance: {money(wallet.balance_uzs, lang)}",
        )
        return {"text": text, "buttons": [_back(lang)], "toast": _t(lang, "Tarif yoqildi", "Plan activated")}

    missing = max(0, price_for(user, plan) - wallet.balance_uzs)
    text = _t(
        lang,
        f"📌 <b>{name}</b> tanlandi.\nYana <b>{money(missing, lang)}</b> to‘lasangiz, tarif avtomatik yoqiladi.",
        f"📌 <b>{name}</b> selected.\nPay <b>{money(missing, lang)}</b> more and it switches on automatically.",
    )
    if user.telegram_id:
        text += _t(lang, f"\nPaynet uchun To‘lov ID: <code>{user.telegram_id}</code>",
                   f"\nPaynet payment ID: <code>{user.telegram_id}</code>")
    return {"text": text, "buttons": [_back(lang)], "toast": _t(lang, "Tanlandi", "Selected")}


def deposit_message(entry_id: int) -> dict:
    """Bot navbatdagi DEPOSIT xabarini YUBORISH paytida chaqiradi.

    → `{"skip": sabab}` yoki `{"caption", "buttons", "offer"}`.
    """
    entry = WalletTransaction.objects.select_related("user").filter(pk=entry_id).first()
    if entry is None:
        return {"skip": "Hamyon yozuvi topilmadi"}
    # Paynet to'lovni shu orada bekor qilgan bo'lsa (masalan `paynet_selftest
    # --full`), "pul tushdi" deyish yolg'on bo'lardi.
    refunded = entry.external_id and WalletTransaction.objects.filter(
        user_id=entry.user_id, kind=WalletTransaction.Kind.REFUND,
        provider=entry.provider, external_id=entry.external_id,
    ).exists()
    if refunded:
        return {"skip": "To'lov bekor qilingan"}

    user = entry.user
    lang = user_lang(user)
    offer = build_offer(user, lang)
    return {
        "caption": deposit_caption(offer, entry, lang),
        "buttons": offer_buttons(offer, lang),
        "offer": offer,
    }

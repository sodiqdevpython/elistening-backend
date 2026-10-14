"""Botdagi "👤 Profil" va "📱 Qurilmalar" — MA'LUMOT va MATN (HTML).

`apps/billing/offer.py` bilan bir xil yondashuv: bu yerda faqat ORM va
matn, tarmoq yo'q. Bot (`bot/handlers/profile.py`) shuni yuboradi va
tugmalarni yasaydi. Shu bois hammasi Django testlari bilan tekshiriladi.

Tugma `callback_data` lari (Telegram cheklovi — 64 bayt):

    pf          profil
    sl          qurilmalar ro'yxati
    sx:<id>     shu qurilmani chiqarish
    sx:all      hamma qurilmadan chiqarish
    pb          (billing) balans va tariflar — `offer.py`
    invite      (bot) taklif havolasi

**To'lov ID** — `User.telegram_id`. Paynet kassasi/ilovasi foydalanuvchini
FAQAT shu raqam orqali taniydi, saytdagi hamyon pill'i ham shuni ko'rsatadi.
Foydalanuvchi talabi: "profile id doim ko'rinib tursin" — u profil
xabarida ham, bot menyusidagi tugmada ham turadi.
"""
from html import escape

from django.db.models import Sum
from django.utils import timezone

from apps.billing.offer import _t, build_offer, fmt_date, money, user_lang

from .models import ActiveSession, DailyActivity

CB_PROFILE = "pf"
CB_SESSIONS = "sl"
CB_REVOKE = "sx"

PLATFORM_LABEL = {
    "web": {"uz": "🌐 Sayt", "en": "🌐 Website"},
    "mobile": {"uz": "📱 Ilova", "en": "📱 App"},
}


def duration(seconds: int, lang: str) -> str:
    """3725 → "1 soat 2 daq" / "1 h 2 min"."""
    minutes = int(seconds or 0) // 60
    hours, minutes = divmod(minutes, 60)
    if hours:
        return _t(lang, f"{hours} soat {minutes} daq", f"{hours} h {minutes} min")
    return _t(lang, f"{minutes} daq", f"{minutes} min")


def _when(moment) -> str:
    return timezone.localtime(moment).strftime("%d.%m.%Y %H:%M") if moment else "—"


def _sessions(user):
    return list(ActiveSession.objects.filter(user=user).order_by("-last_seen_at", "-updated_at"))


def _session_title(session: ActiveSession, lang: str) -> str:
    platform = PLATFORM_LABEL.get(session.platform, {}).get(lang, session.platform)
    return f"{platform} · {session.label}"


# ── Profil ────────────────────────────────────────────────────────────────
def profile_view(user) -> dict:
    """`{"text", "buttons"}` — bot "🌐 Saytda ochish" tugmasini o'zi qo'shadi."""
    lang = user_lang(user)
    offer = build_offer(user, lang)
    from apps.billing.rewards import reward_progress

    invites = reward_progress(user)
    today = timezone.localdate()
    listened = DailyActivity.objects.filter(user=user).aggregate(total=Sum("seconds"))["total"] or 0
    today_row = DailyActivity.objects.filter(user=user, date=today).first()
    sessions = _sessions(user)

    name = escape((user.display_name or "").strip() or "—")
    handle = (user.telegram_username or "").strip()
    lines = [
        _t(lang, "👤 <b>Profil</b>", "👤 <b>Profile</b>"),
        "",
        _t(lang, f"🆔 To‘lov ID: <code>{user.telegram_id}</code>",
           f"🆔 Payment ID: <code>{user.telegram_id}</code>"),
        _t(lang, "<i>Paynet orqali to‘lashda aynan shu raqamni kiriting.</i>",
           "<i>Enter exactly this number when paying via Paynet.</i>"),
        "",
        _t(lang, f"Ism: <b>{name}</b>", f"Name: <b>{name}</b>"),
    ]
    if handle:
        lines.append(f"Telegram: @{escape(handle)}")
    if user.cefr_level:
        lines.append(_t(lang, f"Daraja: <b>{escape(user.cefr_level)}</b>", f"Level: <b>{escape(user.cefr_level)}</b>"))
    lines.append(_t(lang, f"Ro‘yxatdan o‘tgan: {fmt_date(user.date_joined)}",
                    f"Joined: {fmt_date(user.date_joined)}"))

    cur = offer["current"]
    lines.append("")
    if cur is not None:
        plan = escape(cur["name"])
        if cur["paid"] and cur["expires_at"]:
            lines.append(_t(lang, f"⭐ Tarif: <b>{plan}</b> — {fmt_date(cur['expires_at'])} gacha",
                            f"⭐ Plan: <b>{plan}</b> — until {fmt_date(cur['expires_at'])}"))
        elif cur["paid"]:
            lines.append(_t(lang, f"⭐ Tarif: <b>{plan}</b> — muddatsiz", f"⭐ Plan: <b>{plan}</b> — no expiry"))
        else:
            lines.append(_t(lang, f"⭐ Tarif: <b>{plan}</b> (bepul)", f"⭐ Plan: <b>{plan}</b> (free)"))
    lines.append(_t(lang, "💰 Balans: ", "💰 Balance: ") + f"<b>{money(offer['balance_uzs'], lang)}</b>")
    pending = next((c for c in offer["plans"] if c["pending"] and c["buyable"]), None)
    if pending:
        lines.append(_t(lang, f"📌 Tanlangan tarif: <b>{escape(pending['name'])}</b> — pul yetishi bilan yoqiladi",
                        f"📌 Selected plan: <b>{escape(pending['name'])}</b> — turns on once the money arrives"))

    lines += [
        "",
        _t(lang, f"🎧 Tinglash: bugun {duration(today_row.seconds if today_row else 0, lang)} · "
                 f"jami {duration(listened, lang)}",
           f"🎧 Listening: today {duration(today_row.seconds if today_row else 0, lang)} · "
           f"total {duration(listened, lang)}"),
        _t(lang, f"🎁 Taklif qilganlaringiz: <b>{invites['invited_total']}</b>",
           f"🎁 People you invited: <b>{invites['invited_total']}</b>"),
        _t(lang, f"📱 Ulangan qurilmalar: <b>{len(sessions)}</b>",
           f"📱 Signed-in devices: <b>{len(sessions)}</b>"),
    ]
    buttons = [
        [{"text": _t(lang, f"📱 Qurilmalar ({len(sessions)})", f"📱 Devices ({len(sessions)})"), "data": CB_SESSIONS},
         {"text": _t(lang, "💰 Balans va tariflar", "💰 Balance and plans"), "data": "pb"}],
        [{"text": _t(lang, "🎁 Taklif qilish", "🎁 Invite friends"), "data": "invite"}],
    ]
    return {"text": "\n".join(lines), "buttons": buttons}


# ── Qurilmalar ────────────────────────────────────────────────────────────
def sessions_view(user) -> dict:
    lang = user_lang(user)
    sessions = _sessions(user)
    lines = [
        _t(lang, "📱 <b>Ulangan qurilmalar</b>", "📱 <b>Signed-in devices</b>"),
        "",
        _t(lang, "Bir vaqtda 1 ta brauzer va 1 ta telefon kira oladi. Notanish qurilmani "
                 "shu yerdan chiqarib yuboring — u darrov hisobdan chiqadi.",
           "One browser and one phone can be signed in at a time. Sign out any device "
           "you don't recognise — it is logged out immediately."),
        "",
    ]
    buttons: list[list[dict]] = []
    if not sessions:
        lines.append(_t(lang, "Hozir hech qaysi qurilmada kirilmagan.", "No device is signed in right now."))
    for i, s in enumerate(sessions, 1):
        lines.append(f"{i}. <b>{escape(_session_title(s, lang))}</b>")
        detail = _t(lang, f"   Oxirgi faollik: {_when(s.last_seen_at or s.updated_at)}",
                    f"   Last active: {_when(s.last_seen_at or s.updated_at)}")
        if s.ip_address:
            detail += f" · IP {escape(str(s.ip_address))}"
        lines.append(detail)
        buttons.append([{"text": _t(lang, f"🚪 Chiqarish: {s.label}", f"🚪 Sign out: {s.label}")[:60],
                         "data": f"{CB_REVOKE}:{s.pk}"}])
    if len(sessions) > 1:
        buttons.append([{"text": _t(lang, "🚪 Hamma qurilmadan chiqarish", "🚪 Sign out everywhere"),
                         "data": f"{CB_REVOKE}:all"}])
    buttons.append([{"text": _t(lang, "↩️ Profil", "↩️ Profile"), "data": CB_PROFILE}])
    return {"text": "\n".join(lines), "buttons": buttons}


def revoke(user, target: str) -> dict:
    """`sx:<id>` / `sx:all` → `{"toast", "text", "buttons"}` (yangilangan ro'yxat).

    Faqat O'Z sessiyasi — `user` bo'yicha filtr; begona id shunchaki topilmaydi.
    Chiqarilgan qurilmadagi token keyingi so'rovda 401 oladi (`auth.session_ok`).
    """
    lang = user_lang(user)
    qs = ActiveSession.objects.filter(user=user)
    if target == "all":
        removed, _ = qs.delete()
        toast = _t(lang, f"✅ {removed} ta qurilmadan chiqarildi", f"✅ Signed out of {removed} device(s)") \
            if removed else _t(lang, "Qurilma yo'q", "No devices")
    elif target.isdigit():
        session = qs.filter(pk=int(target)).first()
        if session is None:
            toast = _t(lang, "Bu qurilma allaqachon chiqarilgan", "This device is already signed out")
        else:
            label = session.label
            session.delete()
            toast = _t(lang, f"✅ Chiqarildi: {label}", f"✅ Signed out: {label}")
    else:
        toast = ""
    return {"toast": toast[:190], **sessions_view(user)}

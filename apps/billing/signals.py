"""Obuna o'zgarganda: TARIX yozuvi + Telegram xabari.

Bu yagona joy bo'lishi muhim — tarifni kim bergani (to'lov, taklif sovg'asi,
admin qo'li) ahamiyatsiz, tarix ham, xabar ham shu yerdan chiqadi. Aks holda
bir yo'lda tarix yozilib, boshqasida yozilmay qolardi.

Xabar `BotMessage` jadvaliga yoziladi — bot uni navbatdan olib jo'natadi
(`apps/telegrambot`). Shu bois bu yerda tarmoq chaqiruvi YO'Q: admin panelida
obunani saqlash sekinlashmaydi va tarmoq xatosi tranzaksiyani buzmaydi.

Qachon ishlaydi:
  - obuna YANGI yaratilganda, yoki
  - tarif / holat / muddat O'ZGARGANDA.

Yozilmaydi:
  - bepul (default) tarif uchun — bu "ko'tarilish" emas
  - faol bo'lmagan (expired / cancelled) obuna uchun
  - hech narsa o'zgarmagan qayta saqlashda

Telegram xabari yuborilmaydi (lekin tarix baribir yoziladi):
  - foydalanuvchining `telegram_id` si bo'lmasa.
"""
from html import escape

from django.db import transaction
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from .models import Plan, Reason, Subscription, SubscriptionEvent

# `pre_save` da eski holatni eslab qolamiz — `post_save` da nima o'zgarganini
# bilish uchun (Django o'zi "oldingi qiymat" ni bermaydi).
_PREVIOUS: dict[int, tuple[int | None, str, object]] = {}


@receiver(pre_save, sender=Subscription)
def _remember_previous(sender, instance: Subscription, **kwargs):
    if not instance.pk:
        return
    old = Subscription.objects.filter(pk=instance.pk).values("plan_id", "status", "expires_at").first()
    if old:
        _PREVIOUS[instance.pk] = (old["plan_id"], old["status"], old["expires_at"])


def _format_expiry(expires_at, lang: str) -> str:
    """"Qachongacha amal qiladi" qatori. Muddatsiz bo'lsa — "cheksiz"."""
    if expires_at is None:
        return "unlimited" if lang == "en" else "muddatsiz"
    return timezone.localtime(expires_at).strftime("%d.%m.%Y")


def _reason_line(reason: str, lang: str) -> str:
    """Tarif QANDAY olingani — xabarda bir qator bo'lib chiqadi."""
    uz = {
        Reason.PAID: "To'lov orqali",
        Reason.INVITE: "Do'stlaringizni taklif qilganingiz uchun 🎁",
        Reason.MANUAL: "Administrator tomonidan",
        Reason.TEST: "Test akkaunt",
    }
    en = {
        Reason.PAID: "Paid subscription",
        Reason.INVITE: "A gift for inviting your friends 🎁",
        Reason.MANUAL: "Granted by an administrator",
        Reason.TEST: "Test account",
    }
    return (en if lang == "en" else uz).get(reason, "")


def _build_text(event: SubscriptionEvent, lang: str) -> str:
    # Bot HTML parse rejimida yuboradi (`bot/main.py` — ParseMode.HTML),
    # shu bois `*qalin*` markdown ISHLAMAYDI (yulduzchalar shundoq chiqadi).
    # Matnga tushadigan hamma qiymat `escape` qilinadi: nomda `<` bo'lsa
    # Telegram xabarni umuman yubormaydi.
    plan_name = escape(event.plan.name_en if lang == "en" else event.plan.name_uz)
    until = _format_expiry(event.expires_at, lang)
    reason = _reason_line(event.reason, lang)

    if lang == "en":
        lines = [f"🎉 Congratulations! Your plan is now <b>{plan_name}</b>."]
        if reason:
            lines.append(reason)
        lines.append(f"Valid until: {until}")
        lines.append("")
        lines.append("Enjoy the higher daily limits!")
    else:
        lines = [f"🎉 Tabriklaymiz! Tarifingiz <b>{plan_name}</b> ga ko'tarildi."]
        if reason:
            lines.append(reason)
        lines.append(f"Amal qilish muddati: {until}")
        lines.append("")
        lines.append("Kunlik limitlaringiz oshdi — marhamat!")
    return "\n".join(lines)


@receiver(post_save, sender=Subscription)
def log_and_notify(sender, instance: Subscription, created: bool, **kwargs):
    previous = _PREVIOUS.pop(instance.pk, None)

    # Faqat FAOL obuna uchun.
    if instance.status != Subscription.Status.ACTIVE:
        return
    # Bepul (default) tarif — "ko'tarilish" emas.
    if getattr(instance.plan, "is_default", False):
        return

    if not created and previous is not None:
        old_plan_id, old_status, old_expires = previous
        changed = (
            old_plan_id != instance.plan_id
            or old_status != instance.status
            or old_expires != instance.expires_at
        )
        if not changed:
            return

    # ── Tarix yozuvi (har doim — telegram bo'lmasa ham) ──
    event = SubscriptionEvent.objects.create(
        user=instance.user,
        plan=instance.plan,
        reason=instance.reason or Reason.MANUAL,
        months=int(getattr(instance, "_grant_months", 0) or 0),
        started_at=timezone.now(),
        expires_at=instance.expires_at,
        note=str(getattr(instance, "_grant_note", "") or "")[:200],
    )
    # `grants.grant_plan` shu yozuvni qaytarishi uchun instansiyaga ilib qo'yamiz.
    instance._event = event

    user = instance.user
    telegram_id = getattr(user, "telegram_id", None)
    if not telegram_id:
        return

    lang = "en" if (getattr(user, "language", "") or "uz").lower() == "en" else "uz"
    text = _build_text(event, lang)

    def _queue():
        # Import shu yerda — ilovalar yuklanish tartibiga bog'liq bo'lmasin.
        from apps.telegrambot.models import BotMessage

        BotMessage.objects.create(
            user=user,
            telegram_id=telegram_id,
            kind=BotMessage.Kind.SYSTEM,
            text=text,
        )

    # Obuna CHINDAN saqlangach yuboriladi — rollback bo'lsa xabar ham ketmaydi.
    transaction.on_commit(_queue)


# ── Kesh tozalash ─────────────────────────────────────────────────────────
# Tariflar keshlanadi (`limits._plans_by_id`, `views.PLANS_CACHE_KEY`) chunki
# ular deyarli o'zgarmaydi, lekin HAR limit tekshiruvida o'qiladi. Admin
# jadvalni o'zgartirsa kesh darrov tozalanishi shart — aks holda yangi
# limitlar 5 daqiqagacha kuchga kirmasdi.


@receiver([post_save, post_delete], sender=Plan)
def _clear_plan_caches(sender, **kwargs):
    from django.core.cache import cache

    from .limits import forget_plans
    from .views import PLANS_CACHE_KEY

    forget_plans()
    cache.delete(PLANS_CACHE_KEY)


@receiver([post_save, post_delete], sender=Subscription)
def _clear_user_plan_cache(sender, instance: Subscription, **kwargs):
    """Obuna o'zgarsa (admin qo'lda ham) o'sha foydalanuvchining keshi ketadi."""
    from .limits import forget_user_plan

    forget_user_plan(instance.user_id)

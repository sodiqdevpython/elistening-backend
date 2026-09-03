"""Taklif tizimi — kim kimni olib kelgani va u uchun xabar/sovg'a.

## Oqim

1. Foydalanuvchi o'z havolasini ulashadi: `t.me/<bot>?start=<INVITE_CODE>`.
2. Yangi odam havolani bosadi → bot `/start ABC123` ni oladi va
   `PendingInvite(telegram_id, inviter)` yozadi (`User` hali yo'q!).
3. O'sha odam kod bilan saytga/ilovaga kiradi → `TelegramVerifyView` uni
   YARATADI va `attach_pending_invite()` chaqiriladi.
4. **Profilni to'ldiradi** (ism + daraja) — `ProfileSetupView`.
5. FAQAT shundan keyin `Invitation` qatori yoziladi (bir odam — bir marta),
   taklif qilganga bot orqali xabar boradi va `redeem_invites()` sovg'ani
   tekshiradi.

**Nega 4-qadam shart:** kod bilan kirishning o'zi "yangi foydalanuvchi"
degani emas — odam kod olib tashlab ketishi mumkin. Foydalanuvchi shuni
so'radi: taklif faqat odam ism va darajani belgilab, chindan ro'yxatdan
o'tgach hisoblansin.

## Nima uchun sanalMAYDI

- o'zini o'zi taklif qilish;
- allaqachon ro'yxatda bo'lgan (eski) foydalanuvchi — `NEW_USER_WINDOW`;
- ikkinchi marta (baza darajasida: `Invitation.invitee` OneToOne).

## Kunlik xabar chegarasi

Blogger bir kunda minglab odam olib kelishi mumkin — har biri uchun xabar
yuborilsa bot ham, foydalanuvchi ham ko'milib qoladi. Shu bois kuniga faqat
dastlabki `DAILY_NOTIFY_MAX` ta taklif uchun xabar boradi; qolganlari
jimgina sanaladi va profilda ko'rinadi.
"""
import logging
from datetime import timedelta
from html import escape

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Invitation, PendingInvite, User

logger = logging.getLogger(__name__)

#: Taklif faqat SHUNCHA vaqt ichida ro'yxatdan o'tgan odam uchun sanaladi.
#: Ya'ni eski foydalanuvchi havolani bosib "yangi taklif" bo'lib qololmaydi.
NEW_USER_WINDOW = timedelta(days=2)

#: Bir kunda eng ko'pi bilan shuncha taklif uchun bot xabar yuboradi.
DAILY_NOTIFY_MAX = 10


def profile_complete(user: User) -> bool:
    """Odam ro'yxatdan o'tishni TUGATGANMI (ism belgilanganmi).

    Kod bilan kirish yetarli emas: foydalanuvchi ism va darajani belgilab,
    chindan foydalanuvchiga aylanishi kerak. Aks holda kimdir botga kirib
    kod olibgina taklif hisobiga tushib qolardi.

    `display_name` — aynan `ProfileSetupView` to'ldiradigan maydon, ilova
    va sayt ham "profil to'liqmi" ni shundan biladi (`needs_setup`).
    """
    return bool((user.display_name or "").strip())


def find_inviter(invite_code: str) -> User | None:
    """Taklif kodi bo'yicha foydalanuvchi (kod bo'sh/noto'g'ri bo'lsa None)."""
    code = (invite_code or "").strip().upper()
    if not code:
        return None
    return User.objects.filter(invite_code=code).first()


def remember_pending_invite(telegram_id: int, invite_code: str) -> bool:
    """Bot `/start <kod>` ni olganda chaqiradi. Hisobga olinsa/saqlansa True.

    Bir telegram_id uchun BITTA kutilayotgan qator — birinchi bosilgan havola
    kuchda qoladi (keyin boshqa havola bosilsa taklif qilgan o'zgarmaydi).

    **Ro'yxatdan o'tib bo'lgan odam ham hisoblanishi mumkin.** Ilgari bu yerda
    `User.objects.filter(telegram_id=...).exists()` bo'lsa darrov `False`
    qaytardi, ya'ni "akkaunt bor — demak taklif emas". Amalda esa tartib
    tez-tez teskari bo'ladi:

        taklif qilgan: "botga kir" -> B o'zi kod oladi -> kod 60 s da
        eskiradi -> B bir-ikki urinib nihoyat kiradi -> KEYIN taklif
        havolasi yuboriladi -> B bosadi -> taklif YO'QOLARDI

    Holbuki B chindan ham yangi va chindan ham taklif qilingan. Endi qaror
    "akkaunt bormi" ga emas, **odam yangimi** ga qarab chiqariladi
    (`NEW_USER_WINDOW`), va bunday holda taklif shu yerning o'zida hisobga
    olinadi — keyingi kirishni kutmaymiz.

    Qo'sh hisob xavfi yo'q: `register_invitation` `Invitation.invitee`
    (OneToOne) va `NEW_USER_WINDOW` tekshiruvlarini o'zi bajaradi.
    """
    inviter = find_inviter(invite_code)
    if inviter is None:
        return False
    if inviter.telegram_id and int(inviter.telegram_id) == int(telegram_id):
        return False  # o'zini o'zi taklif qilish

    existing = User.objects.filter(telegram_id=telegram_id).first()
    if existing is not None:
        # Allaqachon kimdir hisobiga yozilgan — qayta yozmaymiz.
        if Invitation.objects.filter(invitee=existing).exists():
            return False
        # Hali yangi bo'lsa — darrov hisobga olamiz, eski akkaunt bo'lsa yo'q.
        return register_invitation(inviter, existing, Invitation.Source.BOT) is not None

    _, created = PendingInvite.objects.get_or_create(
        telegram_id=telegram_id, defaults={"inviter": inviter},
    )
    return created


def register_invitation(inviter: User, invitee: User, source: str) -> Invitation | None:
    """Taklifni HISOBGA OLADI: yozuv + xabar + sovg'a. Sanalmasa None.

    Idempotent: `Invitation.invitee` OneToOne bo'lgani uchun ikkinchi chaqiruv
    baza darajasida rad etiladi — kodda xato bo'lsa ham qo'sh hisob yo'q.
    """
    if inviter is None or invitee is None or inviter.pk == invitee.pk:
        return None
    if timezone.now() - invitee.date_joined > NEW_USER_WINDOW:
        return None  # yangi emas
    if not profile_complete(invitee):
        # Ro'yxatdan o'tish TUGALLANMAGAN. Kod bilan kirish yetarli emas:
        # odam ism va darajani belgilab, chindan foydalanuvchi bo'lishi kerak
        # (foydalanuvchi talabi). Profil to'ldirilgach `ProfileSetupView`
        # shu funksiyani qayta chaqiradi va taklif o'shanda sanaladi.
        return None
    if Invitation.objects.filter(invitee=invitee).exists():
        return None

    try:
        with transaction.atomic():
            invitation = Invitation.objects.create(
                inviter=inviter, invitee=invitee, source=source,
            )
    except IntegrityError:
        return None  # poyga: boshqa so'rov ulgurdi

    # `invited_by` — qulaylik uchun (admin, eski kod). Hisob `Invitation` da.
    if not invitee.invited_by_id:
        invitee.invited_by = inviter
        invitee.save(update_fields=["invited_by"])

    PendingInvite.objects.filter(telegram_id=invitee.telegram_id).delete()

    _notify_inviter(invitation)
    _try_reward(inviter)
    return invitation


def attach_pending_invite(user: User, source: str = Invitation.Source.BOT) -> Invitation | None:
    """Yangi yaratilgan foydalanuvchi uchun kutilayotgan taklifni qo'llaydi."""
    if not user.telegram_id:
        return None
    pending = PendingInvite.objects.select_related("inviter").filter(
        telegram_id=user.telegram_id,
    ).first()
    if pending is None:
        return None
    return register_invitation(pending.inviter, user, source)


# ── Ichki yordamchilar ────────────────────────────────────────────────────

def _todays_count(inviter: User) -> int:
    today = timezone.localdate()
    return Invitation.objects.filter(inviter=inviter, created_at__date=today).count()


def _notify_inviter(invitation: Invitation) -> None:
    """Taklif qilganga "siz X ni taklif qildingiz" xabari (kunlik chegara bilan)."""
    inviter = invitation.inviter
    if not inviter.telegram_id:
        return
    if _todays_count(inviter) > DAILY_NOTIFY_MAX:
        return  # blogger holati — jimgina sanaymiz, xabar bermaymiz

    from apps.billing.rewards import reward_progress

    progress = reward_progress(inviter)
    # Bot HTML rejimida yuboradi. Ism FOYDALANUVCHI kiritgan matn, shu bois
    # `escape` majburiy — aks holda `<` bo'lgan ismda xabar umuman ketmaydi.
    raw_name = invitation.invitee.display_name or invitation.invitee.username or "yangi foydalanuvchi"
    name = escape(raw_name)
    lang = (inviter.language or "uz").lower()
    left = progress["next_reward_left"]
    plan = (progress["next_reward_plan"] or "").upper()

    if lang == "en":
        text = (
            f"🎁 <b>{name}</b> joined through your invite link!\n"
            f"Total invited: {progress['invited_total']}"
        )
        if left:
            text += f"\n{left} more to unlock 1 month of {plan}."
    else:
        text = (
            f"🎁 <b>{name}</b> sizning taklif havolangiz orqali qo'shildi!\n"
            f"Jami taklif qilganingiz: {progress['invited_total']}"
        )
        if left:
            text += f"\nYana {left} ta — va 1 oylik {plan} sovg'a bo'ladi."

    _queue_message(inviter, text)
    Invitation.objects.filter(pk=invitation.pk).update(notified=True)


def _try_reward(inviter: User) -> None:
    """Sovg'a tekshiruvi. Xatolik bo'lsa ham ro'yxatdan o'tish buzilmasin."""
    try:
        from apps.billing.rewards import redeem_invites

        redeem_invites(inviter)
    except Exception:
        logger.exception("Taklif sovg'asini hisoblashda xatolik (user=%s)", inviter.pk)


def _queue_message(user: User, text: str) -> None:
    try:
        from apps.telegrambot.models import BotMessage

        BotMessage.objects.create(
            user=user, telegram_id=user.telegram_id,
            kind=BotMessage.Kind.SYSTEM, text=text,
        )
    except Exception:
        logger.exception("Bot xabarini navbatga qo'yib bo'lmadi (user=%s)", user.pk)

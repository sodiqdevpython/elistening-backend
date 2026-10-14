"""Boshqa foydalanuvchilarning OCHIQ profili va qidiruv (sayt).

    GET /api/users/<id>/           ochiq profil (kirmagan ham ko'ra oladi)
    GET /api/users/search/?q=ali   qidiruv (faqat kirganlar, 30/min)

**Faqat ochiq ma'lumot:** ism, rasm, daraja, ro'yxatdan o'tgan sana va
statistika (tinglash vaqti, tugatilgan diktantlar, takliflar soni). Telegram
ID, Telegram username, balans, tarif, qurilmalar — HECH QACHON.

**Avtomatik username yashiriladi.** Telegram orqali kirganlarda username
`tg<chat_id>` bo'ladi — uni ko'rsatish (yoki u bo'yicha qidirish) Telegram ID
ni (= Paynet To'lov ID) oshkor qilardi. Shu bois profil manzili ham username
emas, ichki `id` bo'yicha (`/u/<id>`).

Ro'yxatdan o'tishni tugatmaganlar (ism yo'q) ko'rinmaydi.
"""
import re

from django.db.models import Case, IntegerField, Q, Sum, Value, When
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from .models import DailyActivity, User

AUTO_USERNAME = re.compile(r"^tg\d+$")
SEARCH_LIMIT = 20


def public_username(user) -> str:
    """O'zi tanlagan username; avtomatik `tg<id>` bo'lsa — bo'sh."""
    name = (user.username or "").strip()
    return "" if not name or AUTO_USERNAME.match(name) else name


def avatar_url(request, user) -> str:
    if not user.avatar:
        return ""
    try:
        return request.build_absolute_uri(user.avatar.url)
    except Exception:
        return ""


def public_card(request, user) -> dict:
    """Ro'yxatlar uchun qisqa ko'rinish (qidiruv, takliflar)."""
    name = (user.display_name or "").strip() or public_username(user) or "—"
    return {
        "id": user.pk,
        "name": name,
        "username": public_username(user),
        "avatar_url": avatar_url(request, user),
        "initial": name[:1].upper(),
        "cefr_level": user.cefr_level,
    }


def visible_users():
    """Ochiq ko'rinadigan foydalanuvchilar: faol va ro'yxatdan o'tishni tugatgan."""
    return User.objects.filter(is_active=True).exclude(display_name="")


class SearchThrottle(UserRateThrottle):
    rate = "30/min"


@api_view(["GET"])
@permission_classes([AllowAny])
def user_profile(request, pk: int):
    user = get_object_or_404(visible_users(), pk=pk)
    since = timezone.localdate() - timezone.timedelta(days=29)
    activity = DailyActivity.objects.filter(user=user)
    total = activity.aggregate(s=Sum("seconds"))["s"] or 0
    month = activity.filter(date__gte=since).aggregate(s=Sum("seconds"))["s"] or 0
    from apps.catalog.models import DictationProgress

    completed = DictationProgress.objects.filter(user=user, completions__gt=0).count()
    return Response({
        **public_card(request, user),
        "joined_at": user.date_joined,
        "is_me": request.user.is_authenticated and request.user.pk == user.pk,
        "stats": {
            "listening_seconds": total,
            "listening_seconds_30d": month,
            "dictations_completed": completed,
            "invited_count": user.sent_invites.count(),
        },
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
@throttle_classes([SearchThrottle])
def user_search(request):
    """Ism yoki (o'zi tanlagan) username bo'yicha. Ism boshi mos kelganlar oldinda."""
    q = (request.query_params.get("q") or "").strip().lstrip("@")[:60]
    if len(q) < 2:
        return Response({"results": []})
    users = (
        visible_users()
        .filter(Q(display_name__icontains=q)
                | (Q(username__icontains=q) & ~Q(username__regex=r"^tg[0-9]+$")))
        .annotate(rank=Case(
            When(display_name__istartswith=q, then=Value(0)),
            When(username__istartswith=q, then=Value(1)),
            default=Value(2), output_field=IntegerField(),
        ))
        .order_by("rank", "display_name", "pk")[:SEARCH_LIMIT]
    )
    return Response({"results": [public_card(request, u) for u in users]})


def invite_lists(request, user) -> dict:
    """Profil uchun: men taklif qilganlar (qachon) va meni kim taklif qilgan."""
    from .models import Invitation

    sent = (Invitation.objects.filter(inviter=user).select_related("invitee")
            .order_by("-created_at"))
    invited = [
        {**public_card(request, inv.invitee), "invited_at": inv.created_at}
        for inv in sent[:200]
    ]
    record = Invitation.objects.select_related("inviter").filter(invitee=user).first()
    invited_by = None
    if record is not None:
        inviter = record.inviter
        visible = inviter is not None and inviter.is_active and (inviter.display_name or "").strip()
        invited_by = {
            **(public_card(request, inviter) if visible else
               {"id": None, "name": record.inviter_name or "—", "username": "", "avatar_url": "",
                "initial": (record.inviter_name or "?")[:1].upper(), "cefr_level": ""}),
            "invited_at": record.created_at,
        }
    return {"invited": invited, "invited_by": invited_by}

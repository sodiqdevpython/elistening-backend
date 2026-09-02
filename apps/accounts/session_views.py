"""Sessiyalarni boshqarish — "qaysi qurilmalarda kirganman va ularni chiqarish".

Sayt ham, mobil ilova ham shu uchta endpointdan foydalanadi:

| Endpoint | Nima qiladi |
|---|---|
| `GET  /api/me/sessions/` | Faol sessiyalar ro'yxati (`is_current` bilan) |
| `POST /api/me/sessions/revoke/` | Bittasini chiqaradi (`{"id": N}`) yoki `{"others": true}` bilan qolganlarini |
| `POST /api/auth/logout/` | Shu qurilmaning o'zini chiqaradi |

Qoida o'zgarmaydi: bir vaqtda **1 web + 1 mobil**. Bu ekran yangi ruxsat
bermaydi — u faqat mavjud sessiyani ko'rsatadi va uzib qo'yish imkonini
beradi (masalan telefoni yo'qolgan bo'lsa).

Chiqarish `ActiveSession` qatorini o'chiradi; `auth.session_ok` esa qator
bo'lmasa **rad etadi**, ya'ni o'sha qurilmadagi token darrov 401 bo'ladi.
"""
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import ActiveSession


def _current_sid(request) -> str:
    token = getattr(request, "auth", None)
    if token is None:
        return ""
    try:
        return token.get("sid") or ""
    except Exception:
        return ""


def _row(session: ActiveSession, current_sid: str) -> dict:
    return {
        "id": session.id,
        "platform": session.platform,
        "device": session.label,
        "ip_address": session.ip_address,
        "created_at": session.created_at,
        "last_seen_at": session.last_seen_at,
        "is_current": bool(current_sid) and session.sid == current_sid,
    }


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def my_sessions(request):
    current = _current_sid(request)
    rows = ActiveSession.objects.filter(user=request.user).order_by("-last_seen_at", "-updated_at")
    return Response({"results": [_row(s, current) for s in rows]})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def revoke_session(request):
    """`{"id": N}` — bitta sessiya; `{"others": true}` — o'zidan boshqasi."""
    current = _current_sid(request)
    qs = ActiveSession.objects.filter(user=request.user)

    if request.data.get("others"):
        if not current:
            # sid'siz token bilan "qolganlarini chiqarish" — o'zini ham
            # o'chirib yuborardi. Bunday token endi baribir 401 bo'ladi.
            return Response({"detail": "Sessiya aniqlanmadi"}, status=status.HTTP_400_BAD_REQUEST)
        removed, _ = qs.exclude(sid=current).delete()
        return Response({"revoked": removed})

    session_id = request.data.get("id")
    if not session_id:
        return Response({"detail": "id yoki others kerak"}, status=status.HTTP_400_BAD_REQUEST)

    removed, _ = qs.filter(pk=session_id).delete()
    if not removed:
        return Response({"detail": "Sessiya topilmadi"}, status=status.HTTP_404_NOT_FOUND)
    return Response({"revoked": removed})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def logout(request):
    """Shu qurilmadan chiqish — sessiya qatori o'chadi, token darrov kuchsizlanadi."""
    current = _current_sid(request)
    if current:
        ActiveSession.objects.filter(user=request.user, sid=current).delete()
    return Response({"ok": True})

"""Tariflar API'si.

To'lov OQIMI HOZIRCHA YO'Q. `subscribe` faqat qaysi tarif tanlanganini
belgilaydi va to'lov integratsiyasi ulanmaganini aytadi.
"""
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from .models import Plan
from .serializers import PlanSerializer


@api_view(["GET"])
@permission_classes([AllowAny])
def plans(request):
    qs = Plan.objects.filter(is_active=True).order_by("order", "id")
    return Response(PlanSerializer(qs, many=True).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def subscribe(request):
    plan = Plan.objects.filter(code=request.data.get("plan"), is_active=True).first()
    if not plan:
        return Response({"detail": "Tarif topilmadi"}, status=status.HTTP_404_NOT_FOUND)
    from .grants import grant_plan
    from .models import Reason, Subscription

    # Bepul (default) tarifga QAYTISH — foydalanuvchining o'z tanlovi, shu bois
    # `grant_plan` dan o'tmaydi (u ataylab pastga tushirmaydi).
    if plan.is_default:
        Subscription.objects.update_or_create(
            user=request.user,
            defaults={"plan": plan, "status": Subscription.Status.ACTIVE,
                      "expires_at": None, "reason": Reason.FREE},
        )
        return Response({"ok": True, "plan": plan.code})

    if plan.price_uzs == 0:
        # Narxi 0 bo'lgan aksiya tarifi — 1 oyga beriladi.
        grant_plan(request.user, plan, 1, Reason.MANUAL, note="Narxsiz tarif")
        return Response({"ok": True, "plan": plan.code})

    return Response(
        {"detail": "To'lov tizimi hali ulanmagan. Tez orada Click orqali ishlaydi.",
         "plan": plan.code},
        status=status.HTTP_501_NOT_IMPLEMENTED,
    )

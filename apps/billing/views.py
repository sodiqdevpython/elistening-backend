"""Tariflar API'si.

To'lov **Paynet** orqali (`apps/paynet`). Paynet redirect'li checkout EMAS:
foydalanuvchi Paynet ilovasi/kassasida o'z Telegram chat ID'sini kiritib
to'laydi, pul hamyonga tushadi va tarif shu yerdan yoqiladi.

Shu bois `subscribe` pullik tarif uchun TO'LOV NIYATINI saqlaydi va to'lash
yo'riqnomasini qaytaradi (`apps/billing/wallet_views.py`) — mijoz uchun bitta
endpoint yetarli bo'lsin.
"""
from django.core.cache import cache
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from .models import Plan
from .serializers import PlanSerializer

#: `GET /api/billing/plans/` javobi shu kalitda (signal tozalaydi).
PLANS_CACHE_KEY = "billing_plans_v1"


@api_view(["GET"])
@permission_classes([AllowAny])
def plans(request):
    # Tariflar ro'yxati deyarli o'zgarmaydi, lekin profil/billing sahifasi
    # har ochilganda so'raydi — keshlaymiz (admin o'zgartirsa signal tozalaydi).
    data = cache.get(PLANS_CACHE_KEY)
    if data is None:
        qs = Plan.objects.filter(is_active=True).order_by("order", "id")
        data = PlanSerializer(qs, many=True).data
        cache.set(PLANS_CACHE_KEY, data, 300)
    return Response(data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def subscribe(request):
    plan = Plan.objects.filter(code=request.data.get("plan"), is_active=True).first()
    if not plan:
        return Response({"detail": "Tarif topilmadi"}, status=status.HTTP_404_NOT_FOUND)
    from .grants import grant_plan
    from .limits import forget_user_plan
    from .models import Reason, Subscription

    # Bepul (default) tarifga QAYTISH — foydalanuvchining o'z tanlovi, shu bois
    # `grant_plan` dan o'tmaydi (u ataylab pastga tushirmaydi).
    if plan.is_default:
        Subscription.objects.update_or_create(
            user=request.user,
            defaults={"plan": plan, "status": Subscription.Status.ACTIVE,
                      "expires_at": None, "reason": Reason.FREE},
        )
        forget_user_plan(request.user)
        return Response({"ok": True, "plan": plan.code})

    if plan.price_uzs == 0:
        # Narxi 0 bo'lgan aksiya tarifi — 1 oyga beriladi.
        grant_plan(request.user, plan, 1, Reason.MANUAL, note="Narxsiz tarif")
        return Response({"ok": True, "plan": plan.code})

    # Pullik tarif — Paynet hamyoni orqali. Balans yetsa DARROV yoqiladi,
    # aks holda niyat saqlanib, javobda to'lov yo'riqnomasi qaytadi.
    from .wallet_views import start_purchase

    return start_purchase(request.user, request.data)

"""Sayt/ilova uchun Click checkout — buyurtma yaratib to'lov havolasini beradi.

Click'ning O'ZI bu yerga kelmaydi (u `views.py` ga keladi). Bu endpoint
faqat foydalanuvchi "Click orqali to'lash" tugmasini bosganda chaqiriladi.

    POST /api/billing/click/checkout/  {plan: "plus", months: 1}
    → {order_id, amount_uzs, pay_url}

Mijoz `pay_url` ga o'tkazadi; qolganini Click bajaradi.
"""
from django.conf import settings
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.billing.models import Wallet
from apps.billing.wallet_views import read_plan

from .links import payment_url
from .models import ClickOrder


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def checkout(request):
    """Buyurtma yaratadi (yoki mavjudini qayta ishlatadi) va havola qaytaradi."""
    # `merchant_id` SHART EMAS (`links.py` izohiga qarang) — havola usiz ham
    # yasaladi. Kerak bo'lganlari: servis raqami va imzo kaliti.
    if not (settings.CLICK_SERVICE_ID and settings.CLICK_SECRET_KEY):
        return Response({"detail": "Click hali sozlanmagan"},
                        status=status.HTTP_503_SERVICE_UNAVAILABLE)

    plan, months, error = read_plan(request.data)
    if error is not None:
        return error

    # ── Qancha so'raymiz: to'liq narx EMAS, YETMAYOTGAN qism ────────────
    # Foydalanuvchi ilgari Paynet orqali (yoki qisman Click orqali) pul
    # tashlagan bo'lishi mumkin. To'liq narxni so'rasak u ORTIQCHA to'laydi:
    # pul yo'qolmaydi (hamyonda qoladi), lekin bu kutilmagan xarajat va
    # "nega 23 000 so'rayapti, menda 10 000 bor-ku?" degan savol tug'iladi.
    wallet, _ = Wallet.objects.get_or_create(user=request.user)
    price_uzs = int(plan.price_uzs) * months
    missing_uzs = max(0, price_uzs - wallet.balance_uzs)

    if missing_uzs == 0:
        # Pul allaqachon yetarli — to'lov emas, bitta tugma yetadi.
        return Response(
            {"detail": "Balans yetarli — hisobdagi puldan yoqing",
             "enough": True, "plan": plan.code, "months": months},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Click servis uchun belgilangan minimaldan kichik summani qabul qilmaydi.
    amount_uzs = max(missing_uzs, int(settings.CLICK_MIN_AMOUNT_UZS))

    # Bir xil tarif uchun TO'LANMAGAN buyurtma bo'lsa QAYTA ISHLATAMIZ va
    # summasini yangilaymiz. Nega yangilash muhim: balans o'zgargan bo'lishi
    # mumkin (Paynet'dan pul kelgan), eski buyurtma esa eski summa bilan
    # qolib ketardi va foydalanuvchi eski havolani ochsa noto'g'ri summa
    # ko'rinardi. `INPUT` dan boshqa holatdagilarga TEGMAYMIZ — Click
    # ularni allaqachon ko'rgan.
    order = ClickOrder.objects.filter(
        user=request.user, plan=plan, months=months,
        status=ClickOrder.Status.INPUT,
    ).order_by("-created_at").first()
    if order is None:
        order = ClickOrder.objects.create(
            user=request.user, plan=plan, months=months, amount_uzs=amount_uzs,
        )
    elif int(order.amount_uzs) != amount_uzs:
        order.amount_uzs = amount_uzs
        order.save(update_fields=["amount_uzs", "updated_at"])

    return Response({
        "order_id": order.pk,
        "plan": plan.code,
        "plan_name": plan.status_name,
        "months": months,
        "amount_uzs": amount_uzs,
        "price_uzs": price_uzs,
        # Ortiqcha to'langan qism hamyonda qoladi — mijoz buni ko'rsatishi mumkin.
        "extra_uzs": amount_uzs - missing_uzs,
        "pay_url": payment_url(order),
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def order_status(request, pk: int):
    """Buyurtma holati — foydalanuvchi Click'dan qaytgach sahifa shuni so'raydi.

    To'lov CLICK tomonidan tasdiqlanadi (`views.complete`), brauzer emas.
    Shu bois qaytish sahifasi natijani shu yerdan o'qiydi — `return_url` ga
    ishonmaydi (uni foydalanuvchi qo'lda ham ochishi mumkin).
    """
    order = ClickOrder.objects.filter(pk=pk, user=request.user).first()
    if order is None:
        return Response({"detail": "Buyurtma topilmadi"}, status=status.HTTP_404_NOT_FOUND)
    return Response({
        "order_id": order.pk,
        "status": order.status,
        "paid": order.status == ClickOrder.Status.CONFIRMED,
        "amount_uzs": int(order.amount_uzs),
        "plan": order.plan.code,
        "plan_name": order.plan.status_name,
        "months": order.months,
    })

"""Click to'lov havolasi (Web-oplata / «Платежная ссылка»).

Foydalanuvchi shu havolaga o'tadi, my.click.uz da karta yoki telefon raqamini
kiritadi va to'laydi. **Karta ma'lumoti bizga umuman tegmaydi** — PCI DSS
yuki yo'q, bu usulning asosiy afzalligi.

    https://my.click.uz/services/pay
        ?service_id=<CLICK_SERVICE_ID>
        &merchant_id=<CLICK_MERCHANT_ID>
        &amount=<so'mda>
        &transaction_param=<ClickOrder.pk>      ← merchant_trans_id bo'lib qaytadi
        &return_url=<to'lovdan keyin qaytadigan sahifa>

`transaction_param` — butun integratsiyaning bog'lovchi halqasi: Click uni
`merchant_trans_id` nomi bilan bizning Prepare/Complete endpointimizga
qaytaradi va biz shu orqali qaysi buyurtma to'langanini bilamiz.
"""
from decimal import Decimal
from urllib.parse import urlencode

from django.conf import settings


def payment_url(order) -> str:
    params = {
        "service_id": settings.CLICK_SERVICE_ID,
        # Click so'mda kutadi. `Decimal` ga MAJBURAN aylantiramiz: yangi
        # yaratilgan obyektda `amount_uzs` hali `int` bo'lib turadi (Django
        # maydonni faqat bazadan o'qiganda Decimal qiladi). `normalize`
        # kasrdagi ortiqcha nollarni oladi: 23000.00 → 23000.
        "amount": f"{Decimal(order.amount_uzs).normalize():f}",
        "transaction_param": str(order.pk),
        # Qaytish manziliga buyurtma raqamini ILAMIZ — sahifa qaysi to'lov
        # haqida gap ketayotganini bilib, natijani serverdan so'raydi.
        # `return_url` ning O'ZI hech narsani tasdiqlamaydi: uni foydalanuvchi
        # qo'lda ham ochishi mumkin, to'lovni faqat Click'ning `Complete`
        # so'rovi tasdiqlaydi.
        "return_url": _with_order(settings.CLICK_RETURN_URL, order.pk),
    }
    # `merchant_id` IXTIYORIY. Click'ning O'Z referens kutubxonasida
    # (`click-integration-php/click/configs.php`) u sozlamada turadi, lekin
    # kodning HECH QAYERIDA ishlatilmaydi — na Shop-API imzosida, na Merchant
    # API'da. Kabinetda ham (merchant.click.uz) bunday maydon ko'rinmaydi.
    # Shu bois: berilgan bo'lsa qo'shamiz, bo'lmasa havola usiz ketadi —
    # aks holda butun Click tugmasi mavjud bo'lmagan qiymat tufayli
    # bloklanib qolardi.
    if settings.CLICK_MERCHANT_ID:
        params["merchant_id"] = settings.CLICK_MERCHANT_ID

    return f"{settings.CLICK_PAY_URL}?{urlencode(params)}"


def _with_order(url: str, order_id: int) -> str:
    """`?click_order=<id>` qo'shadi (manzilda allaqachon `?` bo'lsa `&` bilan)."""
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}click_order={order_id}"

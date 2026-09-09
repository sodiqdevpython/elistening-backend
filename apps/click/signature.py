"""Click so'rovining haqiqiyligini tekshirish — MD5 imzo.

**Bu integratsiyaning YAGONA xavfsizlik chegarasi.** Paynet'da IP ro'yxati
bor edi; Click esa ommaviy IP ro'yxati bermaydi va himoya butunlay imzoga
tayanadi. Imzoda `secret_key` qatnashadi — uni bilmagan odam to'g'ri
`sign_string` yasay olmaydi, ya'ni soxta "to'lov o'tdi" so'rovi yubora
olmaydi.

## Formula

    md5(
        click_trans_id
      + service_id
      + SECRET_KEY
      + merchant_trans_id
      + merchant_prepare_id   ← FAQAT action=1 (Complete) da
      + amount
      + action
      + sign_time
    )

Manba — Click'ning rasmiy PHP kutubxonasi (`BasicPaymentsErrors.php`).

## Ikki tuzoq

1. **`amount` XOM SATR bo'lib qatnashadi.** Click "23000.00" yuboradi;
   biz uni `float` ga aylantirib "23000.0" qilsak imzo MOS KELMAYDI va
   har to'lov -1 bilan rad etilardi. Shu bois so'rovdagi satr AYNAN
   ishlatiladi.
2. **Taqqoslash `compare_digest` bilan.** Oddiy `==` bayt-bayt tez chiqib
   ketadi va vaqt bo'yicha oqish beradi.
"""
import hashlib
import secrets

from django.conf import settings


def expected_sign(data, action: str) -> str:
    """So'rov ma'lumotidan kutilayotgan `sign_string` ni hisoblaydi.

    `data` — `request.POST` yoki oddiy lug'at. Qiymatlar SATR sifatida
    olinadi: raqamga aylantirish imzoni buzadi (yuqoridagi 1-tuzoq).
    """
    def field(name: str) -> str:
        value = data.get(name)
        return "" if value is None else str(value)

    parts = [
        field("click_trans_id"),
        field("service_id"),
        settings.CLICK_SECRET_KEY,
        field("merchant_trans_id"),
        # Complete (action=1) da qo'shiladi, Prepare (action=0) da YO'Q.
        field("merchant_prepare_id") if str(action) == "1" else "",
        field("amount"),
        field("action"),
        field("sign_time"),
    ]
    return hashlib.md5("".join(parts).encode("utf-8")).hexdigest()


def is_valid(data, action: str) -> bool:
    given = str(data.get("sign_string") or "")
    if not settings.CLICK_SECRET_KEY or not given:
        # Kalit sozlanmagan bo'lsa HECH QANDAY so'rovni qabul qilmaymiz.
        # "Kalit yo'q — tekshirmaymiz" degan yo'l soxta to'lovga ochiq eshik.
        return False
    return secrets.compare_digest(given.lower(), expected_sign(data, action))

"""Bot qaysi tilda yozadi — BITTA joy.

Bot TO'LIQ o'zbekcha. Foydalanuvchi talabi: *"botda ingliz o'zbek juda aralash
bo'lib ketibdi — o'zbek tilida bo'lsin to'liq bot"*. Ilgari har xabar
`user.language` (SAYT tili) ga qarab tanlanardi: saytni inglizcha ishlatgan
odam profil, balans, tarif tabrigi va "pul tushdi" xabarini inglizcha, menyu
va kod xabarini esa o'zbekcha olardi — bitta chatda ikki til.

Bot xabarini yasaydigan HAR joy tilni shu funksiyadan oladi
(`billing/offer.py`, `billing/signals.py`, `billing/limits.py`,
`billing/wallet.py`, `accounts/invites.py`, `accounts/bot_profile.py`).
Inglizcha matnlar kodda qoldirilgan: bot ikki tilli bo'lishi kerak bo'lsa,
faqat shu funksiyani o'zgartirish kifoya.
"""
BOT_LANG = "uz"


def bot_lang(user=None) -> str:
    return BOT_LANG

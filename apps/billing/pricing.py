"""Foydalanuvchi tarif uchun QANCHA to'lashi kerak — yagona joy.

Ikki qoida bor va ikkalasi ham `grant_plan` ning xulq-atvoridan kelib
chiqadi (`grants.py`):

## 1. Pastga tushirish MUMKIN EMAS

`grant_plan` past tarifni rad etadi. Agar buni faqat o'sha yerda
tekshirsak, foydalanuvchi past tarifni tanlab, pul to'lab, keyin "nega
yoqilmadi?" deb qolardi — pul hamyonda osilib turardi. Shu bois tanlash
BOSQICHIDA to'xtatamiz.

Bir xil tarifni qayta olish MUMKIN — bu uzaytirish (`grant_plan` 2-qoida).

## 2. Ko'tarilishda FARQ olinadi

O'rta (23 000) dan Yuqori (32 000) ga o'tayotgan odam yana to'liq 32 000
to'lamaydi — faqat **farqni** (9 000). Chunki u joriy tarif uchun
allaqachon to'lagan.

> **Bilib qo'yish kerak bo'lgan chetki holat.** Chegirma vaqtga
> bog'lanmagan: obunasi tugashiga 2 kun qolgan odam ham to'liq 23 000
> chegirma oladi va 9 000 ga bir oylik Yuqori oladi. Erta ko'tarilishda
> bu adolatli, kech ko'tarilishda esa daromad yo'qotadi.
>
> Agar buni o'zgartirmoqchi bo'lsangiz — faqat `upgrade_credit` ni
> qolgan kunlarga mutanosib qiling (`sub.expires_at` mavjud):
>
>     qolgan_kun / 30 * current.price_uzs
>
> Boshqa hech qayerga tegish kerak emas: hamma narx shu funksiyadan o'tadi.
"""
from .grants import current_subscription, plan_rank
from .models import Plan


def active_paid_plan(user) -> Plan | None:
    """Foydalanuvchining FAOL va PULLIK tarifi (bo'lmasa `None`).

    Bepul (default) tarif hisobga olinmaydi — u "sotib olingan" emas.
    Muddati tugagan obuna ham `None`: u endi hech qanday huquq bermaydi.
    """
    sub = current_subscription(user)
    if sub is None or not sub.is_active:
        return None
    if sub.plan.is_default or not sub.plan.price_uzs:
        return None
    return sub.plan


def blocking_plan(user, plan: Plan) -> Plan | None:
    """Tanlash MUMKIN EMASmi — sabab bo'lgan joriy tarifni qaytaradi.

    `None` — tanlash mumkin (yuqoriroq, yoki o'shaning o'zini uzaytirish).
    """
    current = active_paid_plan(user)
    if current is not None and plan_rank(plan) < plan_rank(current):
        return current
    return None


def upgrade_credit(user, plan: Plan) -> int:
    """Ko'tarilishdagi chegirma, so'mda (odatiy holatda 0)."""
    current = active_paid_plan(user)
    if current is None:
        return 0
    if plan_rank(plan) <= plan_rank(current):
        # Bir xil tarifni uzaytirishda chegirma YO'Q — yangi oy, to'liq narx.
        return 0
    return int(current.price_uzs)


def price_for(user, plan: Plan, months: int = 1) -> int:
    """Foydalanuvchi shu tarif uchun to'lashi kerak bo'lgan summa (so'm).

    Hamma joy shu funksiyadan foydalanadi: hamyondan yechish
    (`wallet.buy_plan`), Click havolasi (`click/api.checkout`) va mijozga
    ko'rsatiladigan narx (`wallet_views.plan_payload`). Aks holda ular
    ajralib ketib, "tugmada bitta raqam, to'lovda boshqasi" bo'lardi.
    """
    months = max(1, int(months or 1))
    full = int(plan.price_uzs) * months
    return max(0, full - upgrade_credit(user, plan))

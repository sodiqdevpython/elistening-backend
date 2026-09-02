"""Taklif uchun sovg'a tarif — JAMI taklif soniga bog'langan, idempotent hisob.

## Qoidalar (foydalanuvchi aytgan)

| Yig'ilgan taklif | Sovg'a |
|---|---|
| har **20** ta yangi foydalanuvchi | **Plus** 1 oy |
| har **40** ta yangi foydalanuvchi | o'sha 20 lik o'rniga **Pro** 1 oy |

Ya'ni: 20 → Plus, 40 → Pro, 60 → yana Plus, 80 → yana Pro, ... 400 ta taklif
qilgan odam jami **Pro × 10 oy** oladi va `expires_at` shunga qarab uzayadi.

- Faqat CHINDAN yangi (ilgari ro'yxatda bo'lmagan) foydalanuvchi sanaladi —
  buni `Invitation` jadvali kafolatlaydi (`invitee` OneToOne).
- Hisoblagich har 20 tada nolga tushadi, lekin **JAMI taklif soni saqlanadi**
  va profilda ko'rinadi.

## Nega "jami" dan hisoblanadi, "qoldiq" dan emas

Birinchi variantda balans ishlatilgandi: `available = jami - sarflangan`, va
har 20 ta uchun Plus berilardi. Muammo: sovg'a HAR taklifdan keyin
tekshiriladi, ya'ni balans 20 ga yetgan zahoti Plus'ga sarflanardi va **40 ga
hech qachon yetmasdi** — Pro sovg'asi umuman ishlamas edi (400 ta taklif
qilgan blogger ham faqat Plus olardi).

Endi hisob **jami** (`T`) dan chiqadi va sof funksiya:

```
haqli_pro  = T // 40
haqli_plus = (T % 40) // 20
qarz = max(0, haqli - allaqachon_berilgan)     # har tarif uchun alohida
```

`allaqachon_berilgan` — `InviteReward` jadvalidagi oylar yig'indisi. Shu
bois funksiyani necha marta chaqirsangiz ham natija bir xil: qarz nolga
tushgach hech narsa bermaydi. Aynan shu **"hamma Pro bo'lib ketmasin"**
kafolati.

Qo'shimcha xavfsizlik:
  - butun hisob `transaction.atomic()` + `select_for_update()` ichida —
    ikkita parallel so'rov ikki marta sovg'a bermaydi;
  - `grant_plan` pastga tushirmaydi, va u rad etsa `InviteReward` yozilmaydi
    — qarz saqlanib qoladi va keyinroq qayta uriniladi.
"""
from django.db import transaction
from django.db.models import Sum

from .grants import grant_plan
from .models import InviteReward, Plan, Reason

#: Bitta Plus oyi uchun necha taklif.
INVITES_PER_PLUS = 20
#: Bitta Pro oyi uchun necha taklif (Plus'ning butun karrasi bo'lishi shart).
INVITES_PER_PRO = 40

#: Sovg'a beriladigan eng kichik qadam — profil progress bari shunga tayanadi.
REWARD_STEP = INVITES_PER_PLUS


def entitlement(total_invites: int) -> dict[str, int]:
    """`T` ta taklif uchun HAQLI oylar (sof funksiya, bazaga tegmaydi)."""
    total = max(0, int(total_invites))
    return {
        "pro": total // INVITES_PER_PRO,
        "plus": (total % INVITES_PER_PRO) // INVITES_PER_PLUS,
    }


def granted_months(user) -> dict[str, int]:
    """Ledger'dan: shu foydalanuvchiga allaqachon berilgan oylar."""
    rows = (
        InviteReward.objects.filter(user=user)
        .values("plan__code")
        .annotate(total=Sum("months"))
    )
    out = {"pro": 0, "plus": 0}
    for row in rows:
        code = row["plan__code"]
        if code in out:
            out[code] = row["total"] or 0
    return out


def next_reward(total_invites: int) -> tuple[str, int, int]:
    """Keyingi sovg'a: (tarif kodi, qaysi jamida, qancha qolgan)."""
    total = max(0, int(total_invites))
    target = (total // REWARD_STEP + 1) * REWARD_STEP
    plan = "pro" if target % INVITES_PER_PRO == 0 else "plus"
    return plan, target, target - total


def reward_progress(user) -> dict:
    """Profil / bot uchun: jami, berilgan oylar va keyingi sovg'agacha."""
    total = user.invited_count
    plan, target, left = next_reward(total)
    given = granted_months(user)
    return {
        "invited_total": total,
        "granted_plus_months": given["plus"],
        "granted_pro_months": given["pro"],
        # Keyingi sovg'a
        "next_reward_plan": plan,
        "next_reward_at": target,
        "next_reward_left": left,
        # Shu 20 lik ichida nechtasi bor (progress bar uchun: left/REWARD_STEP)
        "step": REWARD_STEP,
        "step_progress": total % REWARD_STEP,
        "tiers": [
            {"plan": "plus", "invites": INVITES_PER_PLUS},
            {"plan": "pro", "invites": INVITES_PER_PRO},
        ],
    }


def redeem_invites(user):
    """Haqli, lekin hali berilmagan sovg'ani beradi. Bergan bo'lsa `InviteReward`.

    Har taklifdan keyin chaqiriladi. Qarz bo'lmasa jimgina `None` qaytaradi —
    ya'ni necha marta chaqirilsa ham ortiqcha tarif berilmaydi.
    """
    from apps.accounts.models import User

    with transaction.atomic():
        # Qatorni qulflaymiz — parallel ikkita taklif ikki marta sovg'a bermasin.
        locked = User.objects.select_for_update().filter(pk=user.pk).first()
        if locked is None:
            return None

        total = locked.invited_count
        due = entitlement(total)
        given = granted_months(locked)

        # Yuqori tarif avval: Pro qarzi bo'lsa o'shani beramiz.
        for code, per in (("pro", INVITES_PER_PRO), ("plus", INVITES_PER_PLUS)):
            months = max(0, due[code] - given[code])
            if not months:
                continue

            plan = Plan.objects.filter(code=code, is_active=True).first()
            if plan is None:
                continue  # bunday tarif yo'q — keyingisini sinaymiz

            note = f"{total} ta taklif -> {plan.name_uz} x {months} oy"
            event = grant_plan(locked, plan, months, Reason.INVITE, note=note)
            if event is None:
                # Foydalanuvchida allaqachon yuqoriroq tarif bor — sovg'a
                # YOZILMAYDI, qarz saqlanadi va keyinroq qayta uriniladi.
                continue

            return InviteReward.objects.create(
                user=locked, plan=plan, months=months,
                invites_spent=months * per, event=event,
            )
    return None

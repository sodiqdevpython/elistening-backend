"""Tariflarni AVTOMATIK yaratadi/yangilaydi — `migrate` paytida ishlaydi.

Shu bois deploy'da qo'lda hech qanday buyruq kerak emas: `git pull` + rebuild
qilinsa, web konteyner `migrate` ni ishga tushiradi va 3 tarif (status) bazada
paydo bo'ladi. Idempotent (update_or_create) — foydalanuvchi/kontentga tegmaydi.

Narx/limit qiymatlari frontend `PlanCards` + mobil `planStatus` bilan mos.
"""
import sys

from django.db import migrations

# code,   nom,           narx,  shorts, video, dictation, ielts, is_default, order
PLANS = [
    ("free", "Qaldirg'och",     0,    8,    2,    2,    0, True,  0),
    ("plus", "Jo'shqin",    23000,   30,   10, None,    2, False, 1),
    ("pro",  "Bo'talog'im", 32000, None, None, None, None, False, 2),
]


def ensure_plans(apps, schema_editor):
    # Test DB'da seed QILMAYMIZ — testlar o'z tariflarini yaratadi (aks holda
    # `Plan.objects.create(code=...)` UNIQUE xatoga uchraydi). Prod/dev
    # `migrate` da esa (`test` buyrug'i emas) tariflar yaratiladi.
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        return
    Plan = apps.get_model("billing", "Plan")
    for code, name, price, sh, vi, di, ie, isdef, order in PLANS:
        Plan.objects.update_or_create(
            code=code,
            defaults=dict(
                name_uz=name, name_en=name, price_uzs=price,
                is_default=isdef, order=order,
                daily_shorts_limit=sh, daily_video_limit=vi,
                daily_dictation_limit=di, daily_ielts_limit=ie,
            ),
        )


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0003_invitereward_subscriptionevent_subscription_reason_and_more"),
    ]

    operations = [
        migrations.RunPython(ensure_plans, migrations.RunPython.noop),
    ]

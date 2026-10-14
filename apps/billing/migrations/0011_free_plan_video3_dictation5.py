"""Tekin tarif: kuniga 3 ta video, 5 ta diktant.

Foydalanuvchi aniqlashtirdi (0010 dagi "20 ta diktant" o'rniga):
*"kuniga n ta video degani — shu bo'yicha savollarga javob berish (exam),
tekin tarif uchun 3 ta; diktant esa orqasidan yozish — free tarif uchun 5 ta
yetadi"*.

Bu safar qiymat ANIQ o'rnatiladi (faqat ko'tarish emas) — 0010 diktantni 20
qilib qo'ygan bo'lishi mumkin. Matnlar `PlanCards.tsx` va `offer.PLAN_COPY`
bilan bir xil. Tariflar keshi TTL bilan 5 daqiqada yangilanadi.
"""
import sys

from django.db import migrations

FREE_VIDEO_LIMIT = 3
FREE_DICTATION_LIMIT = 5


def forwards(apps, schema_editor):
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        return
    Plan = apps.get_model("billing", "Plan")
    Plan.objects.filter(code="free").update(
        daily_video_limit=FREE_VIDEO_LIMIT,
        daily_dictation_limit=FREE_DICTATION_LIMIT,
        features_uz=["Kuniga 8 ta Shorts", f"Kuniga {FREE_VIDEO_LIMIT} ta video",
                     f"Kuniga {FREE_DICTATION_LIMIT} ta diktant"],
        features_en=["8 Shorts per day", f"{FREE_VIDEO_LIMIT} videos per day",
                     f"{FREE_DICTATION_LIMIT} dictations per day"],
    )


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0010_free_dictation_limit_no_ads"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]

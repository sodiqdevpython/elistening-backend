"""Tekin tarif: kuniga 20 ta diktant (2 edi) + tarif matnlaridan "reklama" olib tashlandi.

Foydalanuvchi talabi: *"tekin akkauntda ham 20+ per day bo'lsa yaxshi edi va
tizimda umuman reklama yo'q hozircha — u kerak emas"*. Tarif kartalarida
"Reklama bilan" / "Reklamasiz" yozilgan edi, lekin saytda ham, ilovada ham
reklama yo'q — bu noto'g'ri va'da edi.

- Limit faqat KO'TARILADI: admin allaqachon 20 dan ko'p (yoki cheksiz) qo'ygan
  bo'lsa tegilmaydi.
- `features_uz/en` — `GET /api/billing/plans/` qaytaradi; saytdagi kartalar
  (`PlanCards.tsx`) va bot rasmi (`offer.PLAN_COPY`) bilan bir xil matn.

Tariflar keshi (`plans_by_id_v1_*`, `user_plan_v1_*`) TTL bilan 5 daqiqada
yangilanadi — migratsiyada signal ishlamaydi.
"""
import sys

from django.db import migrations

FREE_DICTATION_LIMIT = 20

FEATURES = {
    "free": (
        ["Kuniga 8 ta Shorts", "Kuniga 2 ta video", f"Kuniga {FREE_DICTATION_LIMIT} ta diktant"],
        ["8 Shorts per day", "2 videos per day", f"{FREE_DICTATION_LIMIT} dictations per day"],
    ),
    "plus": (
        ["Kuniga 30 ta Shorts", "Kuniga 10 ta video", "Cheksiz diktant", "Kuniga 2 ta IELTS test"],
        ["30 Shorts per day", "10 videos per day", "Unlimited dictation", "2 IELTS tests per day"],
    ),
    "pro": (
        ["Cheksiz Shorts", "Cheksiz video", "Cheksiz diktant", "Cheksiz IELTS test"],
        ["Unlimited Shorts", "Unlimited videos", "Unlimited dictation", "Unlimited IELTS tests"],
    ),
}


def forwards(apps, schema_editor):
    # Test DB'da tariflar yo'q (0004 ham u yerda seed qilmaydi) — testlar o'zi yaratadi.
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        return
    Plan = apps.get_model("billing", "Plan")
    (Plan.objects.filter(code="free", daily_dictation_limit__lt=FREE_DICTATION_LIMIT)
     .update(daily_dictation_limit=FREE_DICTATION_LIMIT))
    for code, (uz, en) in FEATURES.items():
        Plan.objects.filter(code=code).update(features_uz=uz, features_en=en)


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0009_paymentsdashboard"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]

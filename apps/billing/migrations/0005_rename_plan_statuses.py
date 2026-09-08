"""Mavjud tariflar nomini yangi STATUS nomlariga o'tkazadi (deploy'da avtomatik):

    Qaldirg'och -> Oddiy,  Jo'shqin -> O'rta,  Bo'talog'im -> Yuqori

Ko'rsatiladigan nom aslida `Plan.status_name` (code'dan) orqali chiqadi, lekin
bazadagi `name_uz`/`name_en` ham mos bo'lsin (admin, eski javoblar). Idempotent
— code bo'yicha yangilaydi. Testlarda o'tkazib yuboriladi.
"""
import sys

from django.db import migrations

NAMES = {"free": "Oddiy", "plus": "O'rta", "pro": "Yuqori"}


def apply(apps, schema_editor):
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        return
    Plan = apps.get_model("billing", "Plan")
    for code, name in NAMES.items():
        Plan.objects.filter(code=code).update(name_uz=name, name_en=name)


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0004_ensure_plans"),
    ]

    operations = [
        migrations.RunPython(apply, migrations.RunPython.noop),
    ]

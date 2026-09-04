"""`Short.is_vertical` — player tik (9:16) yoki keng (16:9) chizilishi.

Qoida havoladan chiqadi: `/shorts/` bo'lsa tik, aks holda keng. Mavjud
yozuvlar ham shu qoida bo'yicha to'ldiriladi — Filmlar/Yangiliklar bo'limiga
oddiy `watch?v=` havolasi bilan qo'shilganlari endi keng player oladi.
"""
from django.db import migrations, models


def set_is_vertical(apps, schema_editor):
    Short = apps.get_model("catalog", "Short")
    # `/shorts/` bo'lganlar tik qoladi (default `True`), qolganlari keng.
    Short.objects.exclude(youtube_link__icontains="/shorts/").update(is_vertical=False)
    Short.objects.filter(youtube_link__icontains="/shorts/").update(is_vertical=True)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0023_ieltslisteningtest_parts_json'),
    ]

    operations = [
        migrations.AddField(
            model_name='short',
            name='is_vertical',
            field=models.BooleanField(db_index=True, default=True, help_text="Havoladan avtomatik aniqlanadi: /shorts/ bo'lsa tik (9:16), aks holda oddiy keng video (16:9).", verbose_name='Tik video (Shorts)'),
        ),
        migrations.RunPython(set_is_vertical, noop),
    ]

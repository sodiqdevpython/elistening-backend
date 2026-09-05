"""MAVJUD kontentni tuzatadi — `migrate` paytida (deploy'da avtomatik, qo'lda
buyruq YO'Q):

1. Video-turdagi (film/multfilm/news/video) YouTube diktantlarga `is_media=True`
   — aks holda `?media=1` feed (mobil bosh sahifa, /movies, /cartoons) ularni
   ko'rsatmaydi va admin `is_media` ni belgilamagan bo'lsa video umuman chiqmaydi.
2. Bo'sh sarlavhali shortlarga oEmbed orqali sarlavha (VPS'da yt-dlp meta
   sarlavhani bermay qo'ygan edi). Best-effort — tarmoq xatosi migratsiyani
   buzmaydi.
"""
import json
import sys
import urllib.parse
import urllib.request

from django.db import migrations

VIDEO_TYPES = ["movie", "cartoon", "news", "random_video"]


def _oembed_title(url):
    try:
        oe = ("https://www.youtube.com/oembed?url="
              + urllib.parse.quote(url, safe="") + "&format=json")
        req = urllib.request.Request(oe, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=4) as r:
            return (json.loads(r.read().decode()).get("title") or "").strip()
    except Exception:
        return ""


def backfill(apps, schema_editor):
    # Testlarda tarmoqqa chiqmaymiz / ma'lumot o'zgartirmaymiz.
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        return

    Dictation = apps.get_model("catalog", "Dictation")
    Short = apps.get_model("catalog", "Short")

    # 1) Video diktantlar = media
    Dictation.objects.filter(
        type__in=VIDEO_TYPES, is_media=False,
    ).exclude(youtube_link="").update(is_media=True)

    # 2) Bo'sh sarlavhali shortlar (oEmbed, best-effort)
    for s in Short.objects.filter(title="").exclude(youtube_link=""):
        title = _oembed_title(s.youtube_link)
        if title:
            s.title = title[:250]
            s.save(update_fields=["title"])


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0026_delete_cartoonvideo_delete_movievideo_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]

"""Tashqi manbadan import qilingan darslar (`Dictation.source_url` bo'sh emas).

Ikki vazifa:

* **Bitta belgi bilan yashirish/ko'rsatish** — "Sayt sozlamalari" →
  "Tashqi manbadan olingan darslar ko'rinsin". Hamma ochiq so'rovlar
  allaqachon `is_published=True` ni tekshiradi, shu bois bitta UPDATE
  yetarli — hech qaysi endpoint "unutilib" qolmaydi.
* **Qayta olmaslik** — scraper bazada bor darsni qayta yuklamaydi
  (`imported_source_ids`).
"""
import re

from django.core.cache import cache

from .models import Dictation

#: Ro'yxatlar keshi (`legacy_views`, `views.dictation_types`) — yashirilgan
#: darslar TTL kutmasdan darrov yo'qolsin.
LIST_CACHE_KEYS = ("home_payload_v1", "categories_v1", "dictation_types_v1")

#: dailydictation dars havolasidagi raqam: `.../1-first-snowfall.1/listen-and-type`
_SOURCE_ID = re.compile(r"\.(\d+)/listen-and-type")
#: Eski (lokal) import'lar faqat slug bilan belgilangan: `dd-<id>-...`
_LEGACY_SLUG = re.compile(r"^dd-(\d+)-")


def imported():
    return Dictation.objects.exclude(source_url="")


def set_imported_visibility(visible: bool) -> int:
    """Import qilingan HAMMA darsni ko'rsatadi/yashiradi → nechta qator."""
    count = imported().update(is_published=bool(visible))
    cache.delete_many(LIST_CACHE_KEYS)
    return count


def imported_source_ids() -> set[int]:
    """Bazada allaqachon bor dailydictation darslarining ID'lari."""
    ids = set()
    for url in imported().values_list("source_url", flat=True).iterator():
        match = _SOURCE_ID.search(url)
        if match:
            ids.add(int(match.group(1)))
    for slug in Dictation.objects.filter(slug__startswith="dd-").values_list("slug", flat=True):
        match = _LEGACY_SLUG.match(slug)
        if match:
            ids.add(int(match.group(1)))
    return ids

"""Tashqi manbadan import (`scrape_dailydictation`) va ularni bitta belgi
bilan yashirish. Tarmoqqa chiqilmaydi — sof funksiyalar va baza."""
import importlib

from django.apps import apps as django_apps
from django.core.cache import cache
from django.test import TestCase

from apps.accounts.models import User
from apps.catalog.imported import LIST_CACHE_KEYS, imported_source_ids, set_imported_visibility
from apps.catalog.management.commands.scrape_dailydictation import (
    ai_signals_off, build_body, parse_category, save_lesson, select,
)
from apps.catalog.models import AIJob, Dictation
from apps.common.models import SiteConfig

URL = "https://dailydictation.com/exercises/numbers/phone-numbers.344/listen-and-type"

CATEGORY_HTML = """
<button data-bs-target="#lessonGroup1" class="x">Section 1 (2 lessons)</button>
<a class="text-decoration-none" href="/exercises/short-stories/1-first-snowfall.1/listen-and-type">
  <span class="fw-semibold">1. First snowfall</span></a>
<small>21 parts</small><small>Vocab level: A1</small>
<a class="text-decoration-none" href="/exercises/short-stories/10-joes-first-car.18/listen-and-type">
  <span class="fw-semibold">10. Joe&#039;s first car</span></a>
<small>21 parts</small><small>Vocab level: B1</small>
<span class=" fs-4" title="PRO lesson">💎</span>
"""


def record(lesson_id=344, url=URL, challenges=None, audio=""):
    return {
        "source": {"id": lesson_id, "slug": "phone-numbers", "type": "number", "level": "",
                   "title": "Phone numbers", "url": url},
        "lessonName": "Phone numbers",
        "audioSrc": audio,
        "youtubeVideoId": "" if audio else "abcdefghijk",
        "challenges": challenges if challenges is not None else [
            {"position": 2, "content": "(465) 210-7035", "timeStart": 4.7, "timeEnd": 9, "hints": []},
            {"position": 1, "content": " Osaka  Today ", "timeStart": 0.12, "timeEnd": 2.73,
             "hints": ["<b>Osaka</b>", " "]},
        ],
    }


class ParsingTests(TestCase):
    def test_category_page(self):
        lessons = parse_category("short-stories", CATEGORY_HTML)
        self.assertEqual([x["id"] for x in lessons], [1, 18])
        self.assertEqual(lessons[0]["level"], "A1")
        self.assertFalse(lessons[0]["pro"])
        self.assertTrue(lessons[1]["pro"])
        self.assertEqual(lessons[1]["title"], "10. Joe's first car")
        self.assertEqual(lessons[0]["group"], "Section 1")
        self.assertEqual(lessons[0]["type"], "short_story")

    def test_body_keeps_source_chunks_and_hints(self):
        body = build_body(record()["challenges"])
        self.assertEqual([c["text"] for c in body], ["Osaka Today", "(465) 210-7035"])
        self.assertEqual(body[0], {"start_ms": 120, "end_ms": 2730, "text": "Osaka Today",
                                   "fixed": True, "hints": ["Osaka"]})
        self.assertNotIn("hints", body[1])


class SelectTests(TestCase):
    def setUp(self):
        self.catalog = {
            "short-stories": [
                {"id": 1, "level": "A1", "pro": False}, {"id": 2, "level": "A1", "pro": True},
                {"id": 3, "level": "B1", "pro": False}, {"id": 4, "level": "B1", "pro": False},
            ],
            "numbers": [{"id": 9, "level": "", "pro": False}],
        }

    def ids(self, *args, **kw):
        return [x["id"] for x in select(self.catalog, *args, **kw)]

    def test_count_means_new_lessons(self):
        """Bazada bori o'tkazib yuboriladi — "1" keyingi YANGI darsni beradi."""
        self.assertEqual(self.ids(1, set()), [1, 9])
        self.assertEqual(self.ids(1, {1, 9}), [2])
        self.assertEqual(self.ids(None, {1, 2}), [3, 4, 9])

    def test_filters(self):
        self.assertEqual(self.ids(1, set(), per_level=True), [1, 3, 9])
        self.assertEqual(self.ids(None, set(), levels={"B1"}), [3, 4])
        self.assertEqual(self.ids(None, set(), skip_pro=True), [1, 3, 4, 9])


class SaveLessonTests(TestCase):
    def test_new_then_update_keeps_admin_visibility(self):
        self.assertEqual(save_lesson(record(), "", publish=True), "new")
        d = Dictation.objects.get()
        self.assertEqual((d.source_url, d.slug, d.type), (URL, "dd-344-phone-numbers", "number"))
        self.assertTrue(d.is_published)
        self.assertEqual(d.youtube_link, "https://www.youtube.com/watch?v=abcdefghijk")
        self.assertEqual(d.transcription_status, "done")

        Dictation.objects.filter(pk=d.pk).update(is_published=False)  # admin yashirdi
        self.assertEqual(save_lesson(record(), "", publish=True), "updated")
        self.assertEqual(Dictation.objects.count(), 1)
        self.assertFalse(Dictation.objects.get().is_published)

    def test_new_lesson_respects_hidden(self):
        save_lesson(record(), "", publish=False)
        self.assertFalse(Dictation.objects.get().is_published)

    def test_empty_lesson_is_skipped(self):
        self.assertEqual(save_lesson(record(challenges=[]), "", publish=True), "empty")
        self.assertFalse(Dictation.objects.exists())

    def test_import_spends_no_ai_tokens(self):
        """Signal o'chirilmasa Whisper/Haiku navbatga tushardi."""
        with ai_signals_off():
            save_lesson(record(), "", publish=True)
        self.assertFalse(AIJob.objects.exists())
        save_lesson(record(lesson_id=345, url=URL.replace(".344", ".345")), "", publish=True)
        self.assertTrue(AIJob.objects.exists())  # tekshiruvning o'zi to'g'ri ishlayotganini isbotlaydi

    def test_known_ids_from_url_and_legacy_slug(self):
        save_lesson(record(), "", publish=True)
        Dictation.objects.create(title="Eski", slug="dd-77-old-one", type="news", body=[{"text": "x"}])
        Dictation.objects.create(title="O'zimizniki", slug="dd-pulse", type="news")
        self.assertEqual(imported_source_ids(), {344, 77})


class VisibilitySwitchTests(TestCase):
    def setUp(self):
        with ai_signals_off():
            save_lesson(record(), "", publish=True)
        self.own = Dictation.objects.create(title="Own", slug="own", type="news", is_published=True)
        self.imported = Dictation.objects.exclude(pk=self.own.pk).get()

    def published(self, obj):
        obj.refresh_from_db()
        return obj.is_published

    def test_switch_hides_only_imported_and_clears_lists(self):
        for key in LIST_CACHE_KEYS:
            cache.set(key, "stale", 600)
        config = SiteConfig.get_solo()
        config.show_imported = False
        config.save()
        self.assertEqual(config._imported_changed, 1)
        self.assertFalse(self.published(self.imported))
        self.assertTrue(self.published(self.own))
        self.assertTrue(all(cache.get(k) is None for k in LIST_CACHE_KEYS))

        config.show_imported = True
        config.save()
        self.assertTrue(self.published(self.imported))

    def test_saving_other_fields_does_not_touch_lessons(self):
        set_imported_visibility(True)
        Dictation.objects.filter(pk=self.imported.pk).update(is_published=False)  # bittasi qo'lda yashirilgan
        config = SiteConfig.get_solo()
        config.contact_telegram = "@someone"
        config.save()
        self.assertIsNone(config._imported_changed)
        self.assertFalse(self.published(self.imported))

    def test_admin_pages(self):
        admin_user = User.objects.create_superuser(username="boss", password="x" * 12)
        self.client.force_login(admin_user)
        listing = self.client.get("/admin/catalog/dictation/?manba=imported")
        self.assertEqual(listing.status_code, 200)
        self.assertContains(listing, "Phone numbers")
        self.assertNotContains(listing, ">Own<")

        # Menyudagi "Sayt sozlamalari" — yagona yozuvni yaratib tahririga yo'naltiradi.
        page = self.client.get("/admin/common/siteconfig/", follow=True)
        self.assertContains(page, "Jami 1 ta")
        response = self.client.post("/admin/common/siteconfig/1/change/",
                                    {"contact_telegram": "", "show_imported": ""}, follow=True)
        self.assertContains(response, "1 ta dars yashirildi")
        self.assertFalse(self.published(self.imported))


class LegacyBackfillTests(TestCase):
    def test_old_local_imports_get_source_url(self):
        Dictation.objects.create(title="Old", slug="dd-1-1-first-snowfall", type="short_story")
        Dictation.objects.create(title="Own", slug="dd-wow", type="news")
        migration = importlib.import_module("apps.catalog.migrations.0028_dictation_source_url")
        migration.backfill(django_apps, None)
        self.assertEqual(
            Dictation.objects.get(slug="dd-1-1-first-snowfall").source_url,
            "https://dailydictation.com/exercises/short-stories/1-first-snowfall.1/listen-and-type",
        )
        self.assertEqual(Dictation.objects.get(slug="dd-wow").source_url, "")

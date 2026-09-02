"""Dictation API testlari."""
import json

from django.test import TestCase

from apps.accounts.models import User
from apps.catalog.models import Dictation, DictationProgress


class ApiTestCase(TestCase):
    def setUp(self):
        self.dictation = Dictation.objects.create(
            title="Sinov darsi", type=Dictation.Type.SHORT_STORY,
            cefr_level="A2",
            body=[
                {"start_ms": 0, "end_ms": 4000, "text": "Hello there."},
                {"start_ms": 4000, "end_ms": 8000, "text": "How are you?"},
            ],
            is_published=True,
        )
        self.draft = Dictation.objects.create(
            title="Qoralama", type=Dictation.Type.CONVERSATION, is_published=False,
        )


class ListingTests(ApiTestCase):
    def test_only_published_dictations_are_listed(self):
        response = self.client.get("/api/dictations/")
        titles = [row["title"] for row in response.json()["results"]]
        self.assertIn("Sinov darsi", titles)
        self.assertNotIn("Qoralama", titles)

    def test_search_filters_results(self):
        response = self.client.get("/api/dictations/?search=Sinov")
        self.assertEqual(response.json()["count"], 1)
        response = self.client.get("/api/dictations/?search=zzzz")
        self.assertEqual(response.json()["count"], 0)

    def test_type_and_level_filters(self):
        response = self.client.get("/api/dictations/?type=news")
        self.assertEqual(response.json()["count"], 0)
        response = self.client.get("/api/dictations/?type=short_story")
        self.assertEqual(response.json()["count"], 1)
        response = self.client.get("/api/dictations/?level=A2")
        self.assertEqual(response.json()["count"], 1)

    def test_list_does_not_include_body(self):
        """Ro'yxatda body yo'q — kichikroq javob."""
        response = self.client.get("/api/dictations/")
        row = response.json()["results"][0]
        self.assertNotIn("body", row)
        # Lekin metadata bor
        self.assertIn("duration_sec", row)
        self.assertIn("chunks_count", row)

    def test_detail_includes_body(self):
        response = self.client.get(f"/api/dictations/{self.dictation.slug}/")
        data = response.json()
        self.assertEqual(len(data["body"]), 2)
        self.assertEqual(data["body"][0]["text"], "Hello there.")

    def test_detail_increments_views(self):
        self.assertEqual(self.dictation.views, 0)
        self.client.get(f"/api/dictations/{self.dictation.slug}/")
        self.dictation.refresh_from_db()
        self.assertEqual(self.dictation.views, 1)

    def test_types_endpoint_returns_counts(self):
        response = self.client.get("/api/dictations/types/")
        by_key = {t["key"]: t for t in response.json()}
        self.assertEqual(by_key["short_story"]["count"], 1)
        self.assertEqual(by_key["news"]["count"], 0)


class FeedParamsTests(TestCase):
    """`?exclude=` va `?random=1` — mobil bosh sahifadagi aralash lenta uchun.

    Lenta har bo'lakda oldin ko'rsatilgan id'larni `exclude=` ga qo'shadi,
    shu bois bir xil video takror chiqmasligi kerak.
    """

    def setUp(self):
        body = [{"start_ms": 0, "end_ms": 1000, "text": "Hi."}]
        self.items = [
            Dictation.objects.create(
                title=f"Video {i}", type=Dictation.Type.SHORT_STORY,
                body=body, is_published=True,
            )
            for i in range(5)
        ]

    def test_media_filter_returns_only_youtube_items(self):
        """`?media=1` — mobil ilova faqat YouTube kontentini ko'rsatadi."""
        body = [{"start_ms": 0, "end_ms": 1000, "text": "Hi."}]
        with_video = Dictation.objects.create(
            title="YouTube video", type=Dictation.Type.SHORT_STORY, body=body,
            is_published=True, is_media=True,
            youtube_link="https://www.youtube.com/watch?v=abc12345678",
        )
        # `is_media` bor, lekin havola bo'sh — bu ham chiqmasligi kerak.
        Dictation.objects.create(
            title="Media bayrogi bor, havola yo'q", type=Dictation.Type.SHORT_STORY,
            body=body, is_published=True, is_media=True, youtube_link="",
        )
        ids = [r["id"] for r in self.client.get("/api/dictations/?media=1").json()["results"]]
        self.assertEqual(ids, [with_video.id])

    def test_without_media_flag_everything_is_listed(self):
        """Bayroqsiz — eski xulq (sayt shunga tayanadi)."""
        self.assertEqual(self.client.get("/api/dictations/").json()["count"], 5)

    def test_exclude_type_removes_those_kinds(self):
        """`?exclude_type=news` — bosh sahifadagi "videolar" bloki uchun."""
        body = [{"start_ms": 0, "end_ms": 1000, "text": "Hi."}]
        news = Dictation.objects.create(
            title="Yangilik", type=Dictation.Type.NEWS, body=body, is_published=True,
        )
        other = Dictation.objects.create(
            title="Oddiy video", type=Dictation.Type.RANDOM_VIDEO, body=body, is_published=True,
        )
        ids = [r["id"] for r in self.client.get("/api/dictations/?exclude_type=news").json()["results"]]
        self.assertIn(other.id, ids)
        self.assertNotIn(news.id, ids)
        # Teskarisi ham ishlashi kerak (news bloki `?type=news` bilan oladi).
        only_news = [r["id"] for r in self.client.get("/api/dictations/?type=news").json()["results"]]
        self.assertEqual(only_news, [news.id])

    def test_exclude_removes_listed_ids(self):
        skip = [self.items[0].id, self.items[2].id]
        response = self.client.get("/api/dictations/?exclude=" + ",".join(str(i) for i in skip))
        ids = [row["id"] for row in response.json()["results"]]
        self.assertEqual(response.json()["count"], 3)
        for i in skip:
            self.assertNotIn(i, ids)

    def test_exclude_ignores_garbage_tokens(self):
        response = self.client.get(f"/api/dictations/?exclude=abc,,{self.items[0].id}, x ")
        ids = [row["id"] for row in response.json()["results"]]
        self.assertEqual(response.json()["count"], 4)
        self.assertNotIn(self.items[0].id, ids)

    def test_random_returns_all_items(self):
        """Tartib tasodifiy, lekin hech narsa yo'qolmasligi kerak."""
        response = self.client.get("/api/dictations/?random=1")
        ids = {row["id"] for row in response.json()["results"]}
        self.assertEqual(ids, {d.id for d in self.items})

    def test_default_order_is_newest_first(self):
        """`random` berilmasa eski xulq saqlanadi — sayt shunga tayanadi."""
        response = self.client.get("/api/dictations/")
        ids = [row["id"] for row in response.json()["results"]]
        self.assertEqual(ids, [d.id for d in reversed(self.items)])


class SlugTests(TestCase):
    def test_slug_auto_generated_from_title(self):
        d = Dictation.objects.create(title="Hello World", type=Dictation.Type.SHORT_STORY)
        self.assertEqual(d.slug, "hello-world")

    def test_duplicate_title_gets_datetime_suffix(self):
        """Bir xil sarlavha bo'lsa slug'ga `-YYYYMMDD-HHMM` qo'shiladi."""
        import re
        Dictation.objects.create(title="Same title", type=Dictation.Type.CONVERSATION)
        d2 = Dictation.objects.create(title="Same title", type=Dictation.Type.CONVERSATION)
        # Format: same-title-YYYYMMDD-HHMM
        self.assertTrue(
            re.match(r"^same-title-\d{8}-\d{4}(-\d+)?$", d2.slug),
            f"Kutilmagan slug: {d2.slug}",
        )
        self.assertNotEqual(d2.slug, "same-title")

    def test_explicit_slug_preserved(self):
        d = Dictation.objects.create(
            title="Title here", slug="my-custom-slug", type=Dictation.Type.NEWS,
        )
        self.assertEqual(d.slug, "my-custom-slug")


class DurationTests(TestCase):
    def test_duration_from_body(self):
        d = Dictation.objects.create(
            title="Test", type=Dictation.Type.SHORT_STORY,
            body=[
                {"start_ms": 0, "end_ms": 3000, "text": "One."},
                {"start_ms": 3000, "end_ms": 7500, "text": "Two."},
            ],
        )
        self.assertEqual(d.duration_ms, 7500)
        self.assertEqual(d.duration_sec, 7)
        self.assertEqual(d.chunks_count, 2)

    def test_empty_body_zero_duration(self):
        d = Dictation.objects.create(title="Empty", type=Dictation.Type.NEWS)
        self.assertEqual(d.duration_ms, 0)
        self.assertEqual(d.chunks_count, 0)


class ProgressTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="tester", password="pw12345!")

    def test_progress_requires_auth(self):
        response = self.client.get(f"/api/dictations/{self.dictation.slug}/progress/")
        self.assertEqual(response.status_code, 401)

    def test_get_progress_defaults_when_none(self):
        self.client.force_login(self.user)
        response = self.client.get(f"/api/dictations/{self.dictation.slug}/progress/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"percent": 0, "last_index": 0, "draft_answers": {}})

    def test_post_creates_and_updates_progress(self):
        self.client.force_login(self.user)
        payload = {"percent": 50, "last_index": 1, "draft_answers": {"1": "hello"}}
        response = self.client.post(
            f"/api/dictations/{self.dictation.slug}/progress/",
            json.dumps(payload), content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        entry = DictationProgress.objects.get(user=self.user, dictation=self.dictation)
        self.assertEqual(entry.percent, 50)
        self.assertEqual(entry.last_index, 1)

    def test_add_time_updates_practiced_time(self):
        response = self.client.post(
            f"/api/dictations/{self.dictation.slug}/add-time/",
            json.dumps({"ms": 5000}), content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.dictation.refresh_from_db()
        self.assertEqual(self.dictation.practiced_time, 5000)

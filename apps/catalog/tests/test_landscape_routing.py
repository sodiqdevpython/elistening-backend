"""Model HAVOLA bo'yicha tanlanadi: `/shorts/` → Short, aks holda Dictation.

Foydalanuvchi katalogdan "Filmlar" ga oddiy YouTube videosi qo'shgan edi va u
saytda ham, ilovada ham TIK Shorts shablonida ochilardi — *"na video na
shorts"*. Sabab: MOVIES/CARTOONS/NEWS bo'limlari HAR DOIM `Short` ga
tushardi.
"""
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase

from apps.catalog.channel_ingest import pick_target
from apps.catalog.models import Dictation, Short

SHORTS_URL = "https://youtube.com/shorts/aaaaaaaaaaa"
WATCH_URL = "https://www.youtube.com/watch?v=bbbbbbbbbbb"


class PickTargetTests(SimpleTestCase):
    def test_movies_section_splits_by_url(self):
        self.assertEqual(pick_target("movies", SHORTS_URL), ("short", Short.ContentType.MOVIE))
        self.assertEqual(pick_target("movies", WATCH_URL), ("dictation", Dictation.Type.MOVIE))

    def test_cartoons_section_splits_by_url(self):
        self.assertEqual(pick_target("cartoons", SHORTS_URL), ("short", Short.ContentType.CARTOON))
        self.assertEqual(pick_target("cartoons", WATCH_URL), ("dictation", Dictation.Type.CARTOON))

    def test_news_section_splits_by_url(self):
        self.assertEqual(pick_target("news", SHORTS_URL), ("short", Short.ContentType.NEWS))
        self.assertEqual(pick_target("news", WATCH_URL), ("dictation", Dictation.Type.NEWS))

    def test_shorts_section_with_a_long_video_becomes_a_video(self):
        """Shorts bo'limiga uzun video qo'yilsa ham u tik shablonga tushmaydi."""
        self.assertEqual(pick_target("shorts", WATCH_URL), ("dictation", Dictation.Type.RANDOM_VIDEO))

    def test_random_videos_always_dictation(self):
        self.assertEqual(pick_target("random_videos", SHORTS_URL),
                         ("dictation", Dictation.Type.RANDOM_VIDEO))

    def test_unknown_section(self):
        self.assertIsNone(pick_target("nope", WATCH_URL))


class ShortValidationTests(TestCase):
    def test_landscape_url_is_rejected(self):
        """Admin formasi oddiy havolani `Short` ga qabul qilmaydi."""
        short = Short(youtube_link=WATCH_URL, content_type=Short.ContentType.MOVIE)
        with self.assertRaises(ValidationError) as ctx:
            short.full_clean(exclude=["title"])
        self.assertIn("youtube_link", ctx.exception.error_dict)

    def test_shorts_url_passes(self):
        short = Short(youtube_link=SHORTS_URL, content_type=Short.ContentType.MOVIE)
        try:
            short.full_clean(exclude=["title"])
        except ValidationError as exc:
            self.assertNotIn("youtube_link", exc.error_dict)


class MoveCommandTests(TestCase):
    def test_moves_landscape_short_to_dictation(self):
        from django.core.management import call_command

        short = Short.objects.create(
            youtube_link=WATCH_URL, content_type=Short.ContentType.MOVIE,
            title="Film", duration_sec=57, full_text="hello",
            mcq_questions=[{"question": "q", "answer": "A"}],
            views=5, likes=2,
        )
        call_command("move_landscape_shorts", "--apply", verbosity=0)

        self.assertFalse(Short.objects.filter(pk=short.pk).exists())
        d = Dictation.objects.get(youtube_link=WATCH_URL)
        self.assertEqual(d.type, Dictation.Type.MOVIE)
        self.assertTrue(d.is_media)
        self.assertEqual(d.mcq_questions, [{"question": "q", "answer": "A"}])
        self.assertEqual(d.views, 5)
        self.assertEqual(d.likes, 2)

    def test_vertical_short_is_left_alone(self):
        from django.core.management import call_command

        short = Short.objects.create(
            youtube_link=SHORTS_URL, content_type=Short.ContentType.MOVIE, title="Tik",
        )
        call_command("move_landscape_shorts", "--apply", verbosity=0)
        self.assertTrue(Short.objects.filter(pk=short.pk).exists())
        self.assertFalse(Dictation.objects.filter(youtube_link=SHORTS_URL).exists())

    def test_dry_run_changes_nothing(self):
        from django.core.management import call_command

        Short.objects.create(
            youtube_link=WATCH_URL, content_type=Short.ContentType.NEWS, title="X",
        )
        call_command("move_landscape_shorts", verbosity=0)
        self.assertEqual(Dictation.objects.count(), 0)
        self.assertEqual(Short.objects.count(), 1)

"""`Short.is_vertical` — player shakli HAVOLADAN aniqlanadi.

Foydalanuvchi shikoyati: Filmlar bo'limiga oddiy YouTube videosi qo'shilgan
edi, lekin u saytda ham, ilovada ham Shorts kabi TIK ko'rsatilardi. Qoida:
URL da `/shorts/` bo'lsa — short (tik), aks holda oddiy video (keng).
`content_type` bunga ta'sir qilmaydi.
"""
from django.test import SimpleTestCase, TestCase

from apps.catalog.models import Short, is_shorts_url


class IsShortsUrlTests(SimpleTestCase):
    def test_shorts_link(self):
        self.assertTrue(is_shorts_url("https://youtube.com/shorts/abc12345678"))
        self.assertTrue(is_shorts_url("https://www.youtube.com/shorts/abc12345678?feature=share"))

    def test_regular_links(self):
        self.assertFalse(is_shorts_url("https://www.youtube.com/watch?v=abc12345678"))
        self.assertFalse(is_shorts_url("https://youtu.be/abc12345678"))
        self.assertFalse(is_shorts_url("https://www.youtube.com/embed/abc12345678"))

    def test_case_insensitive(self):
        self.assertTrue(is_shorts_url("https://YouTube.com/Shorts/abc12345678"))

    def test_empty(self):
        self.assertFalse(is_shorts_url(""))
        self.assertFalse(is_shorts_url(None))


class ShortSaveTests(TestCase):
    def _make(self, link: str, content_type: str = Short.ContentType.SHORT) -> Short:
        return Short.objects.create(
            youtube_link=link, content_type=content_type, title="T", duration_sec=30,
        )

    def test_shorts_url_is_vertical(self):
        self.assertTrue(self._make("https://youtube.com/shorts/aaaaaaaaaaa").is_vertical)

    def test_watch_url_is_landscape(self):
        self.assertFalse(self._make("https://www.youtube.com/watch?v=bbbbbbbbbbb").is_vertical)

    def test_movie_with_regular_url_is_landscape(self):
        """Aynan foydalanuvchi holati: Filmlar + oddiy havola → KENG."""
        obj = self._make("https://www.youtube.com/watch?v=ccccccccccc",
                         Short.ContentType.MOVIE)
        self.assertFalse(obj.is_vertical)

    def test_movie_with_shorts_url_stays_vertical(self):
        """Tur emas, HAVOLA hal qiladi — film ham /shorts/ bo'lsa tik."""
        obj = self._make("https://youtube.com/shorts/ddddddddddd",
                         Short.ContentType.MOVIE)
        self.assertTrue(obj.is_vertical)

    def test_link_change_recomputes(self):
        obj = self._make("https://youtube.com/shorts/eeeeeeeeeee")
        self.assertTrue(obj.is_vertical)
        obj.youtube_link = "https://www.youtube.com/watch?v=eeeeeeeeeee"
        obj.save()
        obj.refresh_from_db()
        self.assertFalse(obj.is_vertical)

    def test_api_exposes_the_flag(self):
        obj = self._make("https://www.youtube.com/watch?v=fffffffffff",
                         Short.ContentType.MOVIE)
        obj.transcription_status = Short.TranscriptionStatus.DONE
        obj.mcq_questions = [{"question": "q", "answer": "A"}]
        obj.save()
        res = self.client.get("/api/shorts/?content_type=movie")
        row = next(r for r in res.data["results"] if r["id"] == obj.pk)
        self.assertIs(row["is_vertical"], False)

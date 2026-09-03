"""`priority` — "kamida BIR MARTA ko'rsatish", "abadiy birinchi" EMAS.

Foydalanuvchi shikoyati: priority'si baland video har kirganda yana birinchi
chiqib, o'chirilmaguncha o'sha yerda turardi. To'g'ri xulq: u ko'rilmaguncha
oldinda, ko'rilgach esa oddiy videolar qatoriga qo'shiladi.
"""
from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.auth import build_tokens
from apps.accounts.models import User
from apps.billing.models import Plan
from apps.catalog.models import Short


def make_short(n: int, priority: int = 0) -> Short:
    return Short.objects.create(
        youtube_id=f"vid{n:08d}",
        youtube_link=f"https://youtu.be/vid{n:08d}",
        title=f"Short {n}",
        duration_sec=30,
        priority=priority,
        is_published=True,
        is_dead=False,
        transcription_status=Short.TranscriptionStatus.DONE,
        mcq_questions=[{"question": "q", "answer": "A"}],
    )


class PriorityTests(TestCase):
    def setUp(self):
        cache.clear()
        Plan.objects.create(
            code="free", name_uz="Bepul", name_en="Free", is_default=True,
            daily_shorts_limit=None,   # limit bu testga aloqasiz
        )
        self.hot = make_short(1, priority=9)
        self.others = [make_short(i) for i in range(2, 8)]
        # `created_at` — `auto_now_add`, ya'ni hammasi bir necha mikrosoniya
        # ichida yaratiladi va ba'zi bazalarda (sqlite) AYNAN teng chiqadi.
        # "Eng yangisi birinchi" testi teng qiymatlarda ma'nosiz bo'lardi,
        # shu bois vaqtlarni ataylab ajratamiz: `hot` — eng eskisi.
        base = timezone.now() - timedelta(days=1)
        Short.objects.filter(pk=self.hot.pk).update(created_at=base)
        for i, obj in enumerate(self.others, start=1):
            Short.objects.filter(pk=obj.pk).update(
                created_at=base + timedelta(minutes=i),
            )

        self.user = User.objects.create(username="u1", telegram_id=1, display_name="U")
        self.client = APIClient()
        tokens = build_tokens(self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

    def _feed_ids(self):
        res = self.client.get("/api/shorts/?random=1&page_size=20")
        return [row["id"] for row in res.data["results"]]

    def test_unseen_priority_comes_first(self):
        self.assertEqual(self._feed_ids()[0], self.hot.id)

    def test_after_watching_it_is_no_longer_pinned(self):
        """Ko'rgandan keyin u oddiy videoga aylanadi — doim birinchi emas."""
        self.assertEqual(self._feed_ids()[0], self.hot.id)

        self.client.post(f"/api/shorts/{self.hot.id}/view/")

        # 12 marta so'raymiz: tasodifiy tartibda u endi turli joylarda chiqadi.
        positions = {self._feed_ids().index(self.hot.id) for _ in range(12)}
        self.assertGreater(
            len(positions), 1,
            "Ko'rilgandan keyin ham doim bir xil (birinchi) joyda turibdi",
        )

    def test_other_users_still_see_it_first(self):
        """Bir odam ko'rgani boshqalarga ta'sir qilmaydi."""
        self.client.post(f"/api/shorts/{self.hot.id}/view/")

        other = User.objects.create(username="u2", telegram_id=2, display_name="B")
        client = APIClient()
        tokens = build_tokens(other)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        res = client.get("/api/shorts/?random=1&page_size=20")
        self.assertEqual(res.data["results"][0]["id"], self.hot.id)

    def test_anonymous_still_gets_priority_first(self):
        """Anonimda tarix yo'q — priority o'z holicha ishlaydi."""
        res = APIClient().get("/api/shorts/?random=1&page_size=20")
        self.assertEqual(res.data["results"][0]["id"], self.hot.id)

    def test_newest_first_feed_also_respects_it(self):
        """`?random` siz lenta (news/movies) ham xuddi shu qoidaga bo'ysunadi."""
        res = self.client.get("/api/shorts/?page_size=20")
        self.assertEqual(res.data["results"][0]["id"], self.hot.id)

        self.client.post(f"/api/shorts/{self.hot.id}/view/")
        res = self.client.get("/api/shorts/?page_size=20")
        # Endi eng yangisi birinchi bo'ladi (hot — eng eskisi)
        self.assertNotEqual(res.data["results"][0]["id"], self.hot.id)

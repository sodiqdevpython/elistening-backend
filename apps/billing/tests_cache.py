"""Kesh — TEZLIK emas, TO'G'RILIK testlari.

Kesh eng xavfli optimizatsiya: u tezlashtiradi, lekin eskirgan qiymat
ko'rsatsa foydalanuvchi "tarifim ko'tarildi, lekin limit eski" yoki
"videoni tugatdim, statistika o'zgarmadi" deydi. Shu bois bu yerda
o'lchov emas, **eskirmasligi** tekshiriladi.

Keshlangan joylar:
  - `limits.get_user_plan`  — obuna o'zgarganda tozalanadi
  - `limits._plans_by_id`   — `Plan` o'zgarganda tozalanadi
  - `views.plans`           — `Plan` o'zgarganda tozalanadi
  - `accounts.my_stats`     — faollik yozilganda tozalanadi
"""
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.auth import build_tokens
from apps.accounts.models import User
from apps.billing import limits
from apps.billing.grants import grant_plan
from apps.billing.models import Plan, Reason, Subscription


class PlanCacheTests(TestCase):
    def setUp(self):
        cache.clear()
        self.free = Plan.objects.create(
            code="free", name_uz="Bepul", name_en="Free", is_default=True,
            daily_shorts_limit=5,
        )
        self.pro = Plan.objects.create(
            code="pro", name_uz="Pro", name_en="Pro", price_uzs=40000,
            daily_shorts_limit=None,
        )
        self.user = User.objects.create(username="u1", telegram_id=1)

    def test_upgrade_takes_effect_immediately(self):
        """Tarif ko'tarilgach ESKI limit qolmasligi kerak (kesh tozalanadi)."""
        self.assertEqual(limits.get_user_plan(self.user).code, "free")

        grant_plan(self.user, self.pro, 1, Reason.PAID)

        self.assertEqual(limits.get_user_plan(self.user).code, "pro")
        self.assertIsNone(limits.snapshot(self.user)["limits"]["shorts"]["limit"])

    def test_admin_editing_a_subscription_clears_the_cache(self):
        """Admin qo'lda obuna yaratsa ham kesh eskirib qolmaydi (signal)."""
        self.assertEqual(limits.get_user_plan(self.user).code, "free")
        Subscription.objects.create(user=self.user, plan=self.pro)
        self.assertEqual(limits.get_user_plan(self.user).code, "pro")

    def test_deleting_a_subscription_clears_the_cache(self):
        Subscription.objects.create(user=self.user, plan=self.pro)
        self.assertEqual(limits.get_user_plan(self.user).code, "pro")
        Subscription.objects.filter(user=self.user).delete()
        self.assertEqual(limits.get_user_plan(self.user).code, "free")

    def test_editing_a_plan_limit_takes_effect(self):
        """Admin tarif limitini o'zgartirsa darrov kuchga kirsin."""
        self.assertEqual(limits.snapshot(self.user)["limits"]["shorts"]["limit"], 5)
        self.free.daily_shorts_limit = 50
        self.free.save()
        self.assertEqual(limits.snapshot(self.user)["limits"]["shorts"]["limit"], 50)

    def test_plans_endpoint_refreshes_after_an_edit(self):
        client = APIClient()
        first = client.get("/api/billing/plans/").data
        self.assertEqual(len(first), 2)

        Plan.objects.create(code="plus", name_uz="Plus", name_en="Plus", price_uzs=1)
        second = client.get("/api/billing/plans/").data
        self.assertEqual(len(second), 3)

    def test_plan_cache_key_changes_with_the_model(self):
        """Kalitga maydonlar ro'yxati kiradi — deploy'da model o'zgarsa eski
        pickle o'qilmaydi (`AttributeError` o'rniga oddiy kesh promashkasi)."""
        key = limits._plans_cache_key()
        self.assertTrue(key.startswith("plans_by_id_v1_"))
        self.assertEqual(key, limits._plans_cache_key())  # barqaror


class ConsumeTests(TestCase):
    """`consume()` ruxsat berilgan yo'lda ORTIQCHA ish qilmasligi kerak."""

    def setUp(self):
        cache.clear()
        Plan.objects.create(code="free", name_uz="Bepul", name_en="Free",
                            is_default=True, daily_shorts_limit=2)
        self.user = User.objects.create(username="u2", telegram_id=2)

    def test_returns_plan_not_snapshot(self):
        allowed, plan = limits.consume(self.user, "shorts", 1)
        self.assertTrue(allowed)
        self.assertEqual(plan.code, "free")

    def test_limit_is_still_enforced(self):
        self.assertTrue(limits.consume(self.user, "shorts", 1)[0])
        self.assertTrue(limits.consume(self.user, "shorts", 2)[0])
        self.assertFalse(limits.consume(self.user, "shorts", 3)[0])

    def test_same_content_twice_is_free(self):
        self.assertTrue(limits.consume(self.user, "shorts", 1)[0])
        self.assertTrue(limits.consume(self.user, "shorts", 1)[0])
        self.assertTrue(limits.consume(self.user, "shorts", 2)[0])
        # Uchinchi NOYOB kontent limitdan oshadi
        self.assertFalse(limits.consume(self.user, "shorts", 3)[0])

    def test_403_body_still_carries_the_snapshot(self):
        """Limitga yetilganda mijozga limit holati baribir yuborilishi shart."""
        # Limitni BOSHQA kontent bilan to'ldiramiz. Raqamlar ataylab katta:
        # pastdagi Short'ning id'si bilan to'qnashsa, `consume` uni "o'sha
        # kontent" deb hisoblab ruxsat berib yuborardi (idempotentlik).
        limits.consume(self.user, "shorts", 90001)
        limits.consume(self.user, "shorts", 90002)

        client = APIClient()
        tokens = build_tokens(self.user)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

        from apps.catalog.models import Short

        short = Short.objects.create(
            youtube_id="zzz11111111", title="X", duration_sec=10,
            is_published=True, transcription_status=Short.TranscriptionStatus.DONE,
            mcq_questions=[{"question": "q", "answer": "A"}],
        )
        res = client.post(f"/api/shorts/{short.id}/view/")
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.data["error"]["code"], "limit_reached")
        self.assertEqual(res.data["limits"]["limits"]["shorts"]["remaining"], 0)


class StatsCacheTests(TestCase):
    def setUp(self):
        cache.clear()
        Plan.objects.create(code="free", name_uz="Bepul", name_en="Free", is_default=True)
        self.user = User.objects.create(username="u3", telegram_id=3)
        self.client = APIClient()
        tokens = build_tokens(self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

    def test_stats_update_right_after_finishing_content(self):
        """Videoni tugatgach statistika DARROV o'zgarishi kerak.

        Aynan shu joyda ilgari shikoyat bo'lgan ("hammasini ko'rsam ham 0
        turibdi"), shu bois kesh yozuvda tozalanadi — TTL kutilmaydi.
        """
        self.assertEqual(self.client.get("/api/me/stats/").data["active_time_seconds"], 0)

        self.client.post("/api/me/activity/track/", {"seconds": 120})

        self.assertEqual(self.client.get("/api/me/stats/").data["active_time_seconds"], 120)

    def test_second_call_is_served_from_cache(self):
        first = self.client.get("/api/me/stats/").data
        with self.assertNumQueries(3):  # faqat auth (user + sessiya + touch)
            second = self.client.get("/api/me/stats/").data
        self.assertEqual(first, second)

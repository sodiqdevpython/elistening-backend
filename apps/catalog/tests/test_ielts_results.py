"""IELTS Listening test — natija saqlash, `my_result`, done filtri, limit.

Yangi qo'shilganlar (`IeltsListeningTestResult` + viewset kengaytmalari):
  - `submit` natijani profilga saqlaydi (update_or_create → 1 qator/user/test)
  - detail/list `my_result` bilan qaytadi
  - `?done=1/0` filtri, `?search=` qidiruv
  - `retrieve` kunlik IELTS limitini tekshiradi (free ielts=0 → 403)
"""
import json

from django.test import TestCase

from apps.accounts.models import User
from apps.billing.models import Plan
from apps.catalog.models import IeltsListeningTest, IeltsListeningTestResult


class IeltsResultTests(TestCase):
    def setUp(self):
        # Default tarif — IELTS cheksiz (retrieve bloklanmasin). Limit testi
        # o'zi `daily_ielts_limit` ni o'zgartiradi.
        self.plan = Plan.objects.create(
            code="free", name_uz="Bepul", name_en="Free", is_default=True,
            daily_ielts_limit=None,
        )
        self.user = User.objects.create_user(username="u", password="pw12345!")
        self.alpha = IeltsListeningTest.objects.create(
            source_url="https://x/alpha", title="Alpha listening", slug="alpha",
            html="<html>alpha</html>", total_questions=2,
            answers={"1": ["Monday"], "2": ["blue"]},
            status=IeltsListeningTest.Status.PARSED, is_published=True,
        )
        self.beta = IeltsListeningTest.objects.create(
            source_url="https://x/beta", title="Beta listening", slug="beta",
            html="<html>beta</html>", total_questions=1,
            answers={"1": ["red"]},
            status=IeltsListeningTest.Status.PARSED, is_published=True,
        )

    def _submit(self, slug, answers):
        return self.client.post(
            f"/api/ielts-tests/{slug}/submit/",
            json.dumps({"answers": answers}), content_type="application/json",
        )

    def test_submit_persists_and_detail_shows_my_result(self):
        self.client.force_login(self.user)
        r = self._submit("alpha", {"1": "Monday", "2": "green"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["score"], 1)  # 1-savol to'g'ri, 2-noto'g'ri

        row = IeltsListeningTestResult.objects.get(user=self.user, test=self.alpha)
        self.assertEqual((row.score, row.total), (1, 2))

        d = self.client.get("/api/ielts-tests/alpha/")
        self.assertEqual(d.status_code, 200)
        mr = d.json()["my_result"]
        self.assertEqual(mr["score"], 1)
        self.assertEqual(mr["results"]["1"], True)
        self.assertEqual(mr["results"]["2"], False)

    def test_resubmit_updates_same_row(self):
        self.client.force_login(self.user)
        self._submit("alpha", {"1": "wrong", "2": "wrong"})
        self._submit("alpha", {"1": "Monday", "2": "blue"})
        rows = IeltsListeningTestResult.objects.filter(user=self.user, test=self.alpha)
        self.assertEqual(rows.count(), 1)          # BITTA qator (yangilanadi)
        self.assertEqual(rows.first().score, 2)

    def test_list_done_filter_and_search(self):
        self.client.force_login(self.user)
        self._submit("alpha", {"1": "Monday", "2": "blue"})  # alpha bajarildi

        done = self.client.get("/api/ielts-tests/?done=1").json()
        self.assertEqual([t["slug"] for t in done["results"]], ["alpha"])
        undone = self.client.get("/api/ielts-tests/?done=0").json()
        self.assertEqual([t["slug"] for t in undone["results"]], ["beta"])

        # my_result ro'yxatda ham bor
        all_ = self.client.get("/api/ielts-tests/").json()
        by_slug = {t["slug"]: t for t in all_["results"]}
        self.assertIsNotNone(by_slug["alpha"]["my_result"])
        self.assertIsNone(by_slug["beta"]["my_result"])

        # qidiruv
        s = self.client.get("/api/ielts-tests/?search=beta").json()
        self.assertEqual([t["slug"] for t in s["results"]], ["beta"])

    def test_retrieve_enforces_daily_ielts_limit(self):
        self.plan.daily_ielts_limit = 0
        self.plan.save()
        self.client.force_login(self.user)
        r = self.client.get("/api/ielts-tests/alpha/")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error"]["code"], "limit_reached")
        self.assertEqual(r.json()["error"]["kind"], "ielts")

    def test_retrieve_allowed_within_limit_and_idempotent(self):
        self.plan.daily_ielts_limit = 1
        self.plan.save()
        self.client.force_login(self.user)
        # 1-test ochiladi (1/1)
        self.assertEqual(self.client.get("/api/ielts-tests/alpha/").status_code, 200)
        # o'sha testni qayta ochish — idempotent, hali ham ruxsat
        self.assertEqual(self.client.get("/api/ielts-tests/alpha/").status_code, 200)
        # YANGI test — limit tugagan
        self.assertEqual(self.client.get("/api/ielts-tests/beta/").status_code, 403)

    def test_anonymous_not_blocked_no_my_result(self):
        # Anonim uchun limit tekshirilmaydi (frontend auth gate bor) va my_result null
        r = self.client.get("/api/ielts-tests/alpha/")
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.json()["my_result"])

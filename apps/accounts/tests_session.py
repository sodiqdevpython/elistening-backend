"""Test akkauntlar (3 tarif) + bir platformada bitta sessiya testlari."""
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import ActiveSession, TestAccountLogin, User
from apps.billing.models import Plan


def _plans():
    Plan.objects.create(code="free", name_uz="Bepul", name_en="Free", is_default=True,
                        daily_shorts_limit=8, daily_video_limit=2)
    Plan.objects.create(code="plus", name_uz="Plus", name_en="Plus", daily_shorts_limit=30, daily_video_limit=10)
    Plan.objects.create(code="pro", name_uz="Pro", name_en="Pro")


class TestAccountTests(TestCase):
    def setUp(self):
        _plans()
        self.client = APIClient()

    def _login(self, code, platform="mobile"):
        return self.client.post("/api/auth/telegram/verify/", {"code": code}, HTTP_X_PLATFORM=platform)

    def test_three_tariffs(self):
        self.assertEqual(self._login("789878").data["user"]["plan"], "free")
        self.assertEqual(self._login("789888").data["user"]["plan"], "plus")
        self.assertEqual(self._login("789898").data["user"]["plan"], "pro")

    def test_logins_recorded(self):
        self._login("789888")
        self._login("789888")
        u = User.objects.get(username="test_plus")
        self.assertEqual(TestAccountLogin.objects.filter(user=u).count(), 2)


class SingleSessionTests(TestCase):
    def setUp(self):
        _plans()

    def _login(self, code, platform):
        c = APIClient()
        r = c.post("/api/auth/telegram/verify/", {"code": code}, HTTP_X_PLATFORM=platform)
        return r.data["access"]

    def _me(self, access, platform):
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION=f"Bearer {access}", HTTP_X_PLATFORM=platform)
        return c.get("/api/me/")

    def test_second_mobile_login_kicks_first(self):
        a1 = self._login("789878", "mobile")
        self.assertEqual(self._me(a1, "mobile").status_code, 200)
        a2 = self._login("789878", "mobile")  # oxirgi kirgan
        # Birinchi mobil token endi ishlamaydi (superseded)
        self.assertEqual(self._me(a1, "mobile").status_code, 401)
        # Ikkinchi (oxirgi) mobil token ishlaydi
        self.assertEqual(self._me(a2, "mobile").status_code, 200)

    def test_web_and_mobile_coexist(self):
        am = self._login("789878", "mobile")
        aw = self._login("789878", "web")  # boshqa platforma — mobil'ni chiqarmaydi
        self.assertEqual(self._me(am, "mobile").status_code, 200)
        self.assertEqual(self._me(aw, "web").status_code, 200)
        # Har platforma uchun bittadan sessiya
        u = User.objects.get(username="test_account")
        self.assertEqual(ActiveSession.objects.filter(user=u).count(), 2)


class SessionManagementTests(TestCase):
    """Sessiyalar ro'yxati va ularni chiqarish (web + mobil bir xil API)."""

    def setUp(self):
        _plans()

    def _client(self, platform):
        c = APIClient()
        r = c.post("/api/auth/telegram/verify/", {"code": "789878"}, HTTP_X_PLATFORM=platform)
        c.credentials(HTTP_AUTHORIZATION=f"Bearer {r.data['access']}", HTTP_X_PLATFORM=platform)
        return c

    def test_list_shows_both_platforms_and_marks_current(self):
        web = self._client("web")
        self._client("mobile")
        res = web.get("/api/me/sessions/")
        self.assertEqual(res.status_code, 200)
        rows = res.data["results"]
        self.assertEqual({r["platform"] for r in rows}, {"web", "mobile"})
        self.assertEqual(sum(1 for r in rows if r["is_current"]), 1)

    def test_revoke_other_session_kills_its_token(self):
        web = self._client("web")
        mobile = self._client("mobile")
        target = next(r for r in web.get("/api/me/sessions/").data["results"]
                      if r["platform"] == "mobile")

        self.assertEqual(web.post("/api/me/sessions/revoke/", {"id": target["id"]}).status_code, 200)
        # Chiqarilgan qurilma darrov 401
        self.assertEqual(mobile.get("/api/me/").status_code, 401)
        self.assertEqual(web.get("/api/me/").status_code, 200)

    def test_revoke_others_keeps_only_current(self):
        web = self._client("web")
        mobile = self._client("mobile")
        res = web.post("/api/me/sessions/revoke/", {"others": True})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["revoked"], 1)
        self.assertEqual(mobile.get("/api/me/").status_code, 401)
        self.assertEqual(web.get("/api/me/").status_code, 200)

    def test_logout_kills_own_token(self):
        web = self._client("web")
        self.assertEqual(web.post("/api/auth/logout/").status_code, 200)
        self.assertEqual(web.get("/api/me/").status_code, 401)
        u = User.objects.get(username="test_account")
        self.assertEqual(ActiveSession.objects.filter(user=u).count(), 0)

    def test_token_without_sid_is_rejected(self):
        """Eski (sid'siz) token abadiy ishlab, ikkinchi sessiyaga yo'l ochardi."""
        from rest_framework_simplejwt.tokens import RefreshToken

        self._client("mobile")
        u = User.objects.get(username="test_account")
        legacy = str(RefreshToken.for_user(u).access_token)  # sid/plat YO'Q
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION=f"Bearer {legacy}", HTTP_X_PLATFORM="mobile")
        self.assertEqual(c.get("/api/me/").status_code, 401)

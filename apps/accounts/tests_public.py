"""Ochiq profil, qidiruv va "kimlarni taklif qilganman" (`public.py`)."""
from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from .invites import register_invitation
from .models import DailyActivity, Invitation, User


def make(tid, name="", username=None, **extra):
    return User.objects.create(username=username or f"tg{tid}", telegram_id=tid, display_name=name, **extra)


class PublicProfileTests(TestCase):
    def setUp(self):
        self.ali = make(931716835, "Ali Valiyev", cefr_level="B2")
        DailyActivity.objects.create(user=self.ali, date=timezone.localdate(), seconds=1200)
        DailyActivity.objects.create(user=self.ali, date=timezone.localdate() - timedelta(days=60), seconds=600)

    def test_guest_sees_only_public_fields(self):
        r = self.client.get(f"/api/users/{self.ali.pk}/")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual((body["name"], body["cefr_level"], body["username"]), ("Ali Valiyev", "B2", ""))
        self.assertEqual(body["stats"]["listening_seconds"], 1800)
        self.assertEqual(body["stats"]["listening_seconds_30d"], 1200)
        self.assertFalse(body["is_me"])
        # Telegram ID (= Paynet To'lov ID) hech qayerda yo'q.
        self.assertNotIn("931716835", r.content.decode())

    def test_custom_username_is_shown(self):
        vali = make(2, "Vali", username="vali_uz")
        self.assertEqual(self.client.get(f"/api/users/{vali.pk}/").json()["username"], "vali_uz")

    def test_unfinished_or_inactive_profiles_are_hidden(self):
        nameless = make(3, "")
        inactive = make(4, "Gone", is_active=False)
        for user in (nameless, inactive):
            self.assertEqual(self.client.get(f"/api/users/{user.pk}/").status_code, 404)

    def test_is_me(self):
        self.client.force_login(self.ali)
        self.assertTrue(self.client.get(f"/api/users/{self.ali.pk}/").json()["is_me"])


class SearchTests(TestCase):
    def setUp(self):
        cache.clear()  # DRF throttle hisobi keshda
        self.me = make(10, "Men")
        self.client.force_login(self.me)
        make(931716835, "Alisher Navoiy")
        make(12, "Zarina Ali")
        make(13, "Bobur", username="ali_dev")
        make(14, "")  # ismsiz — ko'rinmaydi

    def names(self, q):
        return [r["name"] for r in self.client.get("/api/users/search/", {"q": q}).json()["results"]]

    def test_requires_login(self):
        self.client.logout()
        self.assertIn(self.client.get("/api/users/search/", {"q": "ali"}).status_code, (401, 403))

    def test_name_and_custom_username_prefix_first(self):
        self.assertEqual(self.names("ali"), ["Alisher Navoiy", "Bobur", "Zarina Ali"])
        self.assertEqual(self.names("@ali_d"), ["Bobur"])

    def test_short_query_and_auto_username_are_not_searchable(self):
        self.assertEqual(self.names("a"), [])
        # `tg931716835` bo'yicha qidirib Telegram ID'ni topib bo'lmaydi.
        self.assertEqual(self.names("931716"), [])
        self.assertEqual(self.names("tg93"), [])


class InviteListTests(TestCase):
    def setUp(self):
        self.inviter = make(100, "Taklifchi")
        self.a = make(101, "Aziza")
        self.b = make(102, "Bekzod")
        self.first = register_invitation(self.inviter, self.a, Invitation.Source.BOT)
        Invitation.objects.filter(pk=self.first.pk).update(created_at=timezone.now() - timedelta(days=1))
        register_invitation(self.inviter, self.b, Invitation.Source.BOT)

    def test_inviter_sees_who_and_when_newest_first(self):
        self.client.force_login(self.inviter)
        data = self.client.get("/api/me/invites/").json()
        self.assertEqual(data["invited_total"], 2)
        self.assertEqual([r["name"] for r in data["invited"]], ["Bekzod", "Aziza"])
        self.assertTrue(all(r["invited_at"] for r in data["invited"]))
        self.assertIsNone(data["invited_by"])

    def test_invitee_sees_who_invited_them(self):
        self.client.force_login(self.a)
        by = self.client.get("/api/me/invites/").json()["invited_by"]
        self.assertEqual((by["id"], by["name"]), (self.inviter.pk, "Taklifchi"))

    def test_snapshot_survives_inviter_deletion(self):
        self.assertEqual((self.first.inviter_telegram_id, self.first.inviter_name), (100, "Taklifchi"))
        self.inviter.delete()
        record = Invitation.objects.get(invitee=self.a)  # yozuv O'CHMADI
        self.assertIsNone(record.inviter)
        self.assertEqual((record.inviter_telegram_id, record.inviter_name), (100, "Taklifchi"))
        self.a.refresh_from_db()  # `invited_by` ham SET_NULL bo'ldi
        self.client.force_login(self.a)
        by = self.client.get("/api/me/invites/").json()["invited_by"]
        self.assertEqual((by["id"], by["name"]), (None, "Taklifchi"))

    def test_first_link_wins_and_is_never_reassigned(self):
        other = make(103, "Boshqa")
        self.assertIsNone(register_invitation(other, self.a, Invitation.Source.BOT))
        self.assertEqual(Invitation.objects.get(invitee=self.a).inviter, self.inviter)


class LeaderboardPrivacyTests(TestCase):
    def test_auto_username_is_not_exposed(self):
        cache.clear()
        nameless = make(931716835, "")
        DailyActivity.objects.create(user=nameless, date=timezone.localdate(), seconds=100)
        r = self.client.get("/api/leaderboard/")
        row = r.json()["results"][0]
        self.assertEqual((row["username"], row["name"]), ("", "—"))
        self.assertFalse(row["has_profile"])  # ismsiz — ochiq profil yo'q (404)
        self.assertNotIn("931716835", r.content.decode())

    def test_named_user_has_profile_link(self):
        cache.clear()
        ali = make(5, "Ali")
        DailyActivity.objects.create(user=ali, date=timezone.localdate(), seconds=100)
        row = self.client.get("/api/leaderboard/").json()["results"][0]
        self.assertTrue(row["has_profile"])
        self.assertEqual(self.client.get(f"/api/users/{row['user_id']}/").status_code, 200)

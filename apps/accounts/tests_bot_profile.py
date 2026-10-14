"""Botdagi "👤 Profil" va "📱 Qurilmalar" (`bot_profile.py`)."""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.billing.models import Plan, Subscription, Wallet

from .bot_profile import profile_view, revoke, sessions_view
from .models import ActiveSession, DailyActivity, User

UA_CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"


class BotProfileTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="tg931716835", telegram_id=931716835,
                                        telegram_username="sodiq", display_name="Sodiq <b>",
                                        cefr_level="B1")
        Plan.objects.create(code="free", name_uz="Oddiy", name_en="Free", price_uzs=0, is_default=True)
        self.plus = Plan.objects.create(code="plus", name_uz="O'rta", name_en="Plus", price_uzs=23000)

    def buttons(self, view):
        return [b for row in view["buttons"] for b in row]

    def test_profile_shows_payment_id_plan_balance_and_counts(self):
        Wallet.objects.update_or_create(user=self.user, defaults={"balance_tiyin": 1_170_000})
        Subscription.objects.update_or_create(user=self.user, defaults={
            "plan": self.plus, "status": Subscription.Status.ACTIVE,
            "expires_at": timezone.now() + timedelta(days=30)})
        DailyActivity.objects.create(user=self.user, date=timezone.localdate(), seconds=600)
        DailyActivity.objects.create(user=self.user, date=timezone.localdate() - timedelta(days=1), seconds=3000)
        ActiveSession.objects.create(user=self.user, platform="web", sid="a", device=UA_CHROME)

        text = profile_view(self.user)["text"]
        self.assertIn("<code>931716835</code>", text)        # To'lov ID doim bor
        self.assertIn("Sodiq &lt;b&gt;", text)                 # ism HTML-escape
        self.assertIn("@sodiq", text)
        self.assertIn("B1", text)
        self.assertIn("11 700 so‘m", text)
        self.assertIn("gacha", text)                          # pullik tarif muddati
        self.assertIn("bugun 10 daq", text)
        self.assertIn("jami 1 soat 0 daq", text)
        self.assertIn("Ulangan qurilmalar: <b>1</b>", text)
        data = [b["data"] for b in self.buttons(profile_view(self.user))]
        self.assertEqual(data, ["sl", "pb", "invite"])

    def test_free_plan_and_english(self):
        self.user.language = "en"
        self.user.save(update_fields=["language"])
        text = profile_view(self.user)["text"]
        self.assertIn("Payment ID: <code>931716835</code>", text)
        self.assertIn("(free)", text)

    def test_sessions_list_and_revoke_one(self):
        web = ActiveSession.objects.create(user=self.user, platform="web", sid="a", device=UA_CHROME)
        ActiveSession.objects.create(user=self.user, platform="mobile", sid="b", device="okhttp/4.12")
        view = sessions_view(self.user)
        self.assertIn("🌐 Sayt · Chrome · Windows", view["text"])
        data = [b["data"] for b in self.buttons(view)]
        self.assertIn(f"sx:{web.pk}", data)
        self.assertIn("sx:all", data)
        self.assertEqual(data[-1], "pf")

        result = revoke(self.user, str(web.pk))
        self.assertIn("Chiqarildi", result["toast"])
        self.assertEqual(list(ActiveSession.objects.values_list("platform", flat=True)), ["mobile"])
        self.assertNotIn("sx:all", [b["data"] for b in self.buttons(result)])  # bitta qoldi

    def test_cannot_revoke_someone_elses_session(self):
        other = User.objects.create(username="tg2", telegram_id=2, display_name="Other")
        theirs = ActiveSession.objects.create(user=other, platform="web", sid="x", device=UA_CHROME)
        result = revoke(self.user, str(theirs.pk))
        self.assertIn("allaqachon", result["toast"])
        self.assertTrue(ActiveSession.objects.filter(pk=theirs.pk).exists())

    def test_revoke_all_and_empty_state(self):
        ActiveSession.objects.create(user=self.user, platform="web", sid="a", device=UA_CHROME)
        ActiveSession.objects.create(user=self.user, platform="mobile", sid="b")
        result = revoke(self.user, "all")
        self.assertIn("2 ta", result["toast"])
        self.assertFalse(ActiveSession.objects.filter(user=self.user).exists())
        self.assertIn("hech qaysi qurilmada", result["text"])
        self.assertEqual([b["data"] for b in self.buttons(result)], ["pf"])

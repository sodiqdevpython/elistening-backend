"""Hamyon REST API'si (sayt/ilova tomoni) testlari.

Hamyon PROVAYDERDAN MUSTAQIL: Paynet ham, Click ham shu balansga tushadi.
Provayderlarning o'z testlari alohida — `apps/paynet/tests.py` (JSON-RPC) va
`apps/click/tests.py` (Prepare/Complete).

Bu yerda faqat foydalanuvchi ko'radigan oqim: tarif tanlash → to'lov
yo'riqnomasi → balansdan sotib olish.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from .models import Plan, Subscription, Wallet

User = get_user_model()


class WalletApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="tg42", telegram_id=42, display_name="Vali")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        Plan.objects.create(code="free", name_uz="Oddiy", name_en="Free", price_uzs=0,
                            is_default=True)
        self.plus = Plan.objects.create(code="plus", name_uz="O'rta", name_en="Plus",
                                        price_uzs=23000)

    def test_wallet_shows_payment_id(self):
        response = self.client.get("/api/billing/wallet/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["providers"]["paynet"]["payment_id"], "42")
        self.assertEqual(response.data["balance_uzs"], 0)
        self.assertIsNone(response.data["pending"])

    def test_intent_without_money_saves_pending(self):
        response = self.client.post("/api/billing/wallet/intent/", {"plan": "plus", "months": 1})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["activated"])
        self.assertEqual(response.data["pending"]["missing_uzs"], 23000)
        self.assertEqual(Wallet.objects.get(user=self.user).pending_plan, self.plus)

    def test_intent_with_money_activates_immediately(self):
        Wallet.objects.create(user=self.user, balance_tiyin=2_300_000)
        response = self.client.post("/api/billing/wallet/intent/", {"plan": "plus"})
        self.assertTrue(response.data["activated"])
        self.assertEqual(response.data["balance_uzs"], 0)
        self.assertIsNone(response.data["pending"])
        self.assertTrue(Subscription.objects.filter(user=self.user, plan=self.plus).exists())

    def test_buy_without_money_is_402(self):
        response = self.client.post("/api/billing/wallet/buy/", {"plan": "plus"})
        self.assertEqual(response.status_code, 402)
        self.assertFalse(Subscription.objects.filter(user=self.user).exists())

    def test_buy_multiple_months(self):
        Wallet.objects.create(user=self.user, balance_tiyin=7_000_000)
        response = self.client.post("/api/billing/wallet/buy/", {"plan": "plus", "months": 3})
        self.assertEqual(response.status_code, 200)
        # 3 × 23 000 = 69 000 so'm yechiladi, 1 000 so'm qoladi.
        self.assertEqual(response.data["balance_uzs"], 1000)

    def test_months_out_of_range(self):
        response = self.client.post("/api/billing/wallet/buy/", {"plan": "plus", "months": 99})
        self.assertEqual(response.status_code, 400)

    def test_free_plan_is_rejected_here(self):
        response = self.client.post("/api/billing/wallet/intent/", {"plan": "free"})
        self.assertEqual(response.status_code, 400)

    def test_cancel_intent(self):
        self.client.post("/api/billing/wallet/intent/", {"plan": "plus"})
        response = self.client.post("/api/billing/wallet/intent/cancel/")
        self.assertIsNone(response.data["pending"])

    def test_subscribe_routes_paid_plan_through_wallet(self):
        """Eski `/api/billing/subscribe/` endi 501 emas — to'lov yo'riqnomasini beradi."""
        response = self.client.post("/api/billing/subscribe/", {"plan": "plus"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["providers"]["paynet"]["payment_id"], "42")
        self.assertFalse(response.data["activated"])

    def test_anonymous_is_rejected(self):
        self.assertEqual(APIClient().get("/api/billing/wallet/").status_code, 401)

"""Ko'tarilish narxi va pastga tushirish taqiqi.

Ikkalasi ham PUL bilan bog'liq, shu bois har bir qoida alohida sinaladi:
noto'g'ri chegirma bepul tarif tarqatib yuborardi, taqiq ishlamasa esa
foydalanuvchi puli hamyonda osilib qolardi.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from .grants import grant_plan
from .models import Plan, Reason, Subscription, Wallet
from .pricing import active_paid_plan, blocking_plan, price_for, upgrade_credit
from .wallet import buy_plan

User = get_user_model()


class PricingBase(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="tg7", telegram_id=7, display_name="Aziz")
        self.free = Plan.objects.create(code="free", name_uz="Oddiy", name_en="Free",
                                        price_uzs=0, is_default=True)
        self.plus = Plan.objects.create(code="plus", name_uz="O'rta", name_en="Plus",
                                        price_uzs=23000)
        self.pro = Plan.objects.create(code="pro", name_uz="Yuqori", name_en="Pro",
                                       price_uzs=32000)

    def give(self, plan, months=1):
        return grant_plan(self.user, plan, months, Reason.PAID)


class UpgradePriceTests(PricingBase):
    def test_no_subscription_pays_full_price(self):
        self.assertEqual(price_for(self.user, self.plus), 23000)
        self.assertEqual(price_for(self.user, self.pro), 32000)

    def test_upgrade_pays_only_the_difference(self):
        """O'rta -> Yuqori: 32 000 emas, 9 000."""
        self.give(self.plus)
        self.assertEqual(upgrade_credit(self.user, self.pro), 23000)
        self.assertEqual(price_for(self.user, self.pro), 9000)

    def test_same_plan_extension_has_no_discount(self):
        """O'shani uzaytirish — yangi oy, to'liq narx."""
        self.give(self.plus)
        self.assertEqual(price_for(self.user, self.plus), 23000)

    def test_free_plan_gives_no_credit(self):
        Subscription.objects.create(user=self.user, plan=self.free, reason=Reason.FREE)
        self.assertIsNone(active_paid_plan(self.user))
        self.assertEqual(price_for(self.user, self.pro), 32000)

    def test_expired_subscription_gives_no_credit(self):
        """Muddati tugagan obuna chegirma bermaydi — u endi hech narsa emas."""
        self.give(self.plus)
        sub = Subscription.objects.get(user=self.user)
        sub.status = Subscription.Status.EXPIRED
        sub.save(update_fields=["status"])
        self.assertEqual(price_for(self.user, self.pro), 32000)

    def test_multi_month_upgrade(self):
        """Chegirma BIR MARTA olinadi, har oyga emas."""
        self.give(self.plus)
        self.assertEqual(price_for(self.user, self.pro, months=3), 32000 * 3 - 23000)


class DowngradeBlockTests(PricingBase):
    def test_lower_plan_is_blocked(self):
        self.give(self.pro)
        blocker = blocking_plan(self.user, self.plus)
        self.assertIsNotNone(blocker)
        self.assertEqual(blocker.code, "pro")

    def test_same_and_higher_are_allowed(self):
        self.give(self.plus)
        self.assertIsNone(blocking_plan(self.user, self.plus))
        self.assertIsNone(blocking_plan(self.user, self.pro))

    def test_nothing_blocks_without_subscription(self):
        self.assertIsNone(blocking_plan(self.user, self.plus))


class WalletChargeTests(PricingBase):
    def test_upgrade_charges_only_the_difference(self):
        """Hamyondan ham FARQ yechiladi — tugmadagi raqam bilan bir xil."""
        self.give(self.plus)
        Wallet.objects.update_or_create(user=self.user, defaults={"balance_tiyin": 900_000})

        event, wallet = buy_plan(self.user, self.pro, 1)
        self.assertIsNotNone(event)
        self.assertEqual(wallet.balance_tiyin, 0)
        self.assertEqual(Subscription.objects.get(user=self.user).plan, self.pro)

    def test_upgrade_with_full_price_leaves_change(self):
        """To'liq narx to'lagan bo'lsa, ortiqchasi hamyonda qoladi."""
        self.give(self.plus)
        Wallet.objects.update_or_create(user=self.user, defaults={"balance_tiyin": 3_200_000})
        buy_plan(self.user, self.pro, 1)
        self.assertEqual(Wallet.objects.get(user=self.user).balance_tiyin, 2_300_000)


class ApiTests(PricingBase):
    def setUp(self):
        super().setUp()
        self.api = APIClient()
        self.api.force_authenticate(user=self.user)

    def test_wallet_shows_upgrade_price(self):
        self.give(self.plus)
        response = self.api.post("/api/billing/wallet/intent/", {"plan": "pro"})
        self.assertEqual(response.status_code, 200)
        pending = response.data["pending"]
        self.assertEqual(pending["price_uzs"], 9000)
        self.assertEqual(pending["full_price_uzs"], 32000)
        self.assertEqual(pending["upgrade_credit_uzs"], 23000)
        self.assertEqual(pending["missing_uzs"], 9000)

    def test_downgrade_is_refused_by_api(self):
        self.give(self.pro)
        response = self.api.post("/api/billing/wallet/intent/", {"plan": "plus"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["blocked_by"], "pro")
        # Niyat ham saqlanmasligi kerak — foydalanuvchi u yoqqa yo'naltirilmaydi.
        self.assertFalse(Wallet.objects.filter(user=self.user,
                                               pending_plan__isnull=False).exists())

    def test_downgrade_is_refused_by_buy(self):
        self.give(self.pro)
        Wallet.objects.update_or_create(user=self.user, defaults={"balance_tiyin": 5_000_000})
        response = self.api.post("/api/billing/wallet/buy/", {"plan": "plus"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Wallet.objects.get(user=self.user).balance_tiyin, 5_000_000)

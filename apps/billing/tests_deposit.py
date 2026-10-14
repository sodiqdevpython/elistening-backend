"""Pul tushganda: hamyon TARIXI, botdagi xabar va tarif tugmalari.

Diqqat markazida: tarix balans bilan hech qachon ajralmasligi, xabar faqat
pul CHINDAN tushganda ketishi, va bot tugmasi ikki marta bosilsa tarif ikki
marta sotib olinmasligi.
"""
import importlib

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.click.tests import ClickBase
from apps.paynet.models import PaynetTransaction
from apps.paynet.tests import PaynetBase
from apps.telegrambot.models import BotMessage

from . import offer
from .grants import grant_plan
from .models import Plan, Reason, Subscription, SubscriptionEvent, Wallet, WalletTransaction
from .wallet import buy_plan, choose_plan, credit

User = get_user_model()
Kind = WalletTransaction.Kind


def make_plans():
    free = Plan.objects.create(code="free", name_uz="Oddiy", name_en="Free", price_uzs=0, is_default=True)
    plus = Plan.objects.create(code="plus", name_uz="O'rta", name_en="Plus", price_uzs=23000)
    pro = Plan.objects.create(code="pro", name_uz="Yuqori", name_en="Pro", price_uzs=32000)
    return free, plus, pro


def deposit_messages(user):
    return BotMessage.objects.filter(user=user, kind=BotMessage.Kind.DEPOSIT)


class PaynetDepositTests(PaynetBase):
    def setUp(self):
        super().setUp()
        make_plans()

    def test_payment_writes_history_and_queues_message(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.pay(trn_id=900, amount=5_000_000)

        entry = WalletTransaction.objects.get(user=self.user)
        self.assertEqual(entry.kind, Kind.DEPOSIT)
        self.assertEqual(entry.provider, WalletTransaction.Provider.PAYNET)
        self.assertEqual(entry.amount_tiyin, 5_000_000)
        self.assertEqual(entry.balance_after_tiyin, 5_000_000)
        self.assertEqual(entry.external_id, "900")

        message = deposit_messages(self.user).get()
        self.assertEqual(message.payload, {"entry_id": entry.pk})
        self.assertEqual(message.telegram_id, 777)
        self.assertIn("50 000", message.text)

    def test_repeat_request_writes_nothing_twice(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.pay(trn_id=901)
            self.call("PerformTransaction", {"amount": 2_300_000, "serviceId": 1,
                                             "transactionId": 901, "fields": {"client_id": "777"}})
        self.assertEqual(WalletTransaction.objects.filter(user=self.user).count(), 1)
        self.assertEqual(deposit_messages(self.user).count(), 1)

    def test_cancel_writes_refund_and_message_is_skipped(self):
        """`paynet_selftest --full` aynan shunday: yoziladi va darrov bekor qilinadi."""
        with self.captureOnCommitCallbacks(execute=True):
            self.pay(trn_id=902)
        self.result("CancelTransaction", {"serviceId": 1, "transactionId": 902})

        refund = WalletTransaction.objects.get(user=self.user, kind=Kind.REFUND)
        self.assertEqual(refund.amount_tiyin, -2_300_000)
        self.assertEqual(refund.balance_after_tiyin, 0)

        entry_id = deposit_messages(self.user).get().payload["entry_id"]
        self.assertIn("skip", offer.deposit_message(entry_id))

    def test_pending_plan_is_bought_and_message_shows_it(self):
        """Saytda oldindan tanlangan tarif shu to'lov bilan yoqiladi — xabar
        YUBORISH paytida yig'ilgani uchun rasmda u allaqachon JORIY."""
        plus = Plan.objects.get(code="plus")
        Wallet.objects.create(user=self.user, pending_plan=plus)
        with self.captureOnCommitCallbacks(execute=True):
            self.pay(trn_id=903, amount=3_000_000)

        kinds = list(WalletTransaction.objects.filter(user=self.user)
                     .order_by("id").values_list("kind", "amount_tiyin", "balance_after_tiyin"))
        self.assertEqual(kinds, [(Kind.DEPOSIT, 3_000_000, 3_000_000),
                                 (Kind.PURCHASE, -2_300_000, 700_000)])

        data = offer.deposit_message(deposit_messages(self.user).get().payload["entry_id"])
        current = [c["code"] for c in data["offer"]["plans"] if c["current"]]
        self.assertEqual(current, ["plus"])
        self.assertIn("+30 000 so‘m", data["caption"])
        self.assertIn("7 000 so‘m", data["caption"])


class ClickDepositTests(ClickBase):
    def test_complete_writes_deposit_then_purchase(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.do_prepare()
            self.do_complete()

        rows = list(WalletTransaction.objects.filter(user=self.user).order_by("id"))
        self.assertEqual([(r.kind, r.provider, r.amount_tiyin) for r in rows], [
            (Kind.DEPOSIT, WalletTransaction.Provider.CLICK, 2_300_000),
            (Kind.PURCHASE, WalletTransaction.Provider.WALLET, -2_300_000),
        ])
        self.assertEqual(rows[0].external_id, str(self.order.pk))
        self.assertEqual(rows[1].plan, self.plan)
        self.assertEqual(rows[1].balance_after_tiyin, 0)
        self.assertEqual(deposit_messages(self.user).count(), 1)

    def test_repeat_complete_does_not_duplicate(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.do_prepare()
            self.do_complete()
            self.do_complete()
        self.assertEqual(WalletTransaction.objects.filter(user=self.user, kind=Kind.DEPOSIT).count(), 1)
        self.assertEqual(deposit_messages(self.user).count(), 1)


class WalletLedgerTests(TestCase):
    def setUp(self):
        self.free, self.plus, self.pro = make_plans()
        self.user = User.objects.create(username="tg10", telegram_id=10, display_name="Ali")

    def balance(self):
        return Wallet.objects.get(user=self.user).balance_tiyin

    def test_history_always_matches_balance(self):
        credit(self.user, 5_000_000, WalletTransaction.Provider.PAYNET, notify=False)
        buy_plan(self.user, self.plus)
        buy_plan(self.user, self.pro)  # ko'tarilish — faqat farq
        last = WalletTransaction.objects.filter(user=self.user).first()  # eng yangisi
        self.assertEqual(last.balance_after_tiyin, self.balance())
        total = sum(WalletTransaction.objects.filter(user=self.user).values_list("amount_tiyin", flat=True))
        self.assertEqual(total, self.balance())
        self.assertEqual(last.amount_tiyin, -900_000)

    def test_free_purchase_is_not_recorded(self):
        Plan.objects.filter(pk=self.plus.pk).update(price_uzs=0)
        self.plus.refresh_from_db()
        buy_plan(self.user, self.plus)
        self.assertFalse(WalletTransaction.objects.filter(user=self.user).exists())

    def test_user_without_telegram_gets_no_message(self):
        user = User.objects.create(username="web_only")
        with self.captureOnCommitCallbacks(execute=True):
            credit(user, 100_000, WalletTransaction.Provider.ADMIN)
        self.assertFalse(BotMessage.objects.filter(user=user).exists())

    def test_choose_plan_activates_or_remembers(self):
        credit(self.user, 1_000_000, WalletTransaction.Provider.PAYNET, notify=False)
        status, event, wallet = choose_plan(self.user, self.plus)
        self.assertEqual((status, event), ("pending", None))
        self.assertEqual(wallet.pending_plan, self.plus)
        self.assertEqual(self.balance(), 1_000_000)

        credit(self.user, 1_300_000, WalletTransaction.Provider.PAYNET, notify=False)
        status, event, _ = choose_plan(self.user, self.plus)
        self.assertEqual(status, "activated")
        self.assertEqual(self.balance(), 0)

    def test_stale_button_takes_no_money(self):
        """Bot tugmasi ikki marta bosildi — ikkinchisi hech narsa qilmaydi."""
        credit(self.user, 5_000_000, WalletTransaction.Provider.PAYNET, notify=False)
        first = choose_plan(self.user, self.plus, expect_balance_tiyin=5_000_000)
        second = choose_plan(self.user, self.plus, expect_balance_tiyin=5_000_000)
        self.assertEqual(first[0], "activated")
        self.assertEqual(second[0], "stale")
        self.assertEqual(self.balance(), 2_700_000)
        self.assertEqual(SubscriptionEvent.objects.filter(user=self.user).count(), 1)


class OfferTests(TestCase):
    def setUp(self):
        self.free, self.plus, self.pro = make_plans()
        self.user = User.objects.create(username="tg20", telegram_id=20, display_name="Vali")

    def cards(self):
        return {c["code"]: c for c in offer.build_offer(self.user)["plans"]}

    def test_new_user_sees_free_as_current(self):
        cards = self.cards()
        self.assertTrue(cards["free"]["current"])
        self.assertFalse(cards["free"]["buyable"])
        self.assertEqual([c["code"] for c in cards.values()], ["free", "plus", "pro"])
        buttons = [b["data"] for row in offer.offer_buttons(offer.build_offer(self.user), "uz") for b in row]
        self.assertEqual(buttons, ["pl:plus", "pl:pro"])

    def test_upgrade_shows_difference_and_blocks_lower(self):
        grant_plan(self.user, self.plus, 1, Reason.PAID)
        cards = self.cards()
        self.assertTrue(cards["plus"]["current"])
        self.assertEqual(cards["pro"]["pay_uzs"], 9000)
        self.assertTrue(cards["pro"]["upgrade"])
        self.assertTrue(cards["free"]["current"] is False)
        grant_plan(self.user, self.pro, 1, Reason.PAID)
        cards = self.cards()
        self.assertTrue(cards["plus"]["lower"])
        self.assertFalse(cards["plus"]["buyable"])
        self.assertEqual(offer.plan_prompt(self.user, "plus").keys(), {"alert"})

    def test_prompt_then_confirm_buys_once(self):
        credit(self.user, 2_300_000, WalletTransaction.Provider.CLICK, notify=False)
        prompt = offer.plan_prompt(self.user, "plus")
        self.assertIn("Tasdiqlaysizmi", prompt["text"])
        ok = prompt["buttons"][0][0]["data"]
        self.assertEqual(ok, "pk:plus:2300000")
        # Tasdiqlash ekranida pul YECHILMAYDI.
        self.assertEqual(Wallet.objects.get(user=self.user).balance_tiyin, 2_300_000)

        _, code, expect = ok.split(":")
        done = offer.apply_choice(self.user, code, int(expect))
        self.assertIn("yoqildi", done["text"])
        again = offer.apply_choice(self.user, code, int(expect))
        self.assertIn("alert", again)  # eskirgan tugma
        self.assertEqual(Subscription.objects.get(user=self.user).plan, self.plus)
        self.assertEqual(SubscriptionEvent.objects.filter(user=self.user).count(), 1)

    def test_not_enough_money_offers_to_pick(self):
        credit(self.user, 1_000_000, WalletTransaction.Provider.PAYNET, notify=False)
        prompt = offer.plan_prompt(self.user, "pro")
        self.assertIn("22 000 so‘m", prompt["text"])  # yetishmagan qism
        self.assertIn("<code>20</code>", prompt["text"])  # Paynet To'lov ID
        _, code, expect = prompt["buttons"][0][0]["data"].split(":")
        done = offer.apply_choice(self.user, code, int(expect))
        self.assertIn("tanlandi", done["text"])
        self.assertEqual(Wallet.objects.get(user=self.user).pending_plan, self.pro)

    def test_english_site_user_still_gets_uzbek(self):
        """Bot TO'LIQ o'zbekcha — rasm (kartochkalar) ham, matn ham."""
        User.objects.filter(pk=self.user.pk).update(language="en")
        self.user.refresh_from_db()
        entry = credit(self.user, 3_200_000, WalletTransaction.Provider.PAYNET, notify=False)
        data = offer.deposit_message(entry.pk)
        self.assertIn("Hisobingizga", data["caption"])
        self.assertEqual(data["offer"]["lang"], "uz")
        self.assertEqual(data["offer"]["plans"][1]["features"][0], "Kuniga 30 ta Shorts")


class WalletHistoryApiTests(TestCase):
    URL = "/api/billing/wallet/history/"

    def setUp(self):
        self.free, self.plus, _ = make_plans()
        self.user = User.objects.create(username="tg30", telegram_id=30, display_name="Sardor")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_requires_auth(self):
        self.assertEqual(APIClient().get(self.URL).status_code, 401)

    def test_lists_deposits_and_purchases_newest_first(self):
        credit(self.user, 5_000_000, WalletTransaction.Provider.PAYNET, notify=False)
        buy_plan(self.user, self.plus)
        other = User.objects.create(username="tg31", telegram_id=31)
        credit(other, 100_000, WalletTransaction.Provider.CLICK, notify=False)

        data = self.client.get(self.URL).json()
        self.assertEqual(data["balance_uzs"], 27000)
        self.assertEqual(data["total_in_uzs"], 50000)
        self.assertEqual(data["total_spent_uzs"], 23000)
        self.assertEqual([r["kind"] for r in data["results"]], ["purchase", "deposit"])
        self.assertEqual(data["results"][0]["amount_uzs"], -23000)
        self.assertEqual(data["results"][0]["plan"], "plus")
        self.assertEqual(data["results"][1]["provider"], "paynet")
        self.assertEqual(data["results"][1]["balance_after_uzs"], 50000)


class BackfillMigrationTests(TestCase):
    """0008 — jadvaldan oldingi to'lovlar tarixga tiklanadi, takror yozilmaydi."""

    def test_backfill_is_idempotent(self):
        user = User.objects.create(username="tg40", telegram_id=40)
        now = timezone.now()
        PaynetTransaction.objects.create(transaction_id=1, service_id=1, user=user,
                                         amount_tiyin=500_000, performed_at=now)
        PaynetTransaction.objects.create(transaction_id=2, service_id=1, user=user,
                                         amount_tiyin=300_000, performed_at=now,
                                         state=PaynetTransaction.State.CANCELLED, cancelled_at=now)
        migration = importlib.import_module("apps.billing.migrations.0008_backfill_wallet_history")

        migration.backfill(django_apps, None)
        migration.backfill(django_apps, None)

        rows = sorted(WalletTransaction.objects.filter(user=user)
                      .values_list("external_id", "kind", "amount_tiyin"))
        self.assertEqual(rows, [("1", "deposit", 500_000), ("2", "deposit", 300_000),
                                ("2", "refund", -300_000)])

"""Taklif tizimi va sovg'a tariflari.

Eng muhim tekshiruv — **hech kim bepulga Pro bo'lib qolmasin**: bitta taklif
faqat bir marta sanaladi, bir marta sarflanadi va qayta-qayta chaqirilganda
yangi sovg'a bermaydi.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.accounts.invites import (
    DAILY_NOTIFY_MAX, attach_pending_invite, register_invitation, remember_pending_invite,
)
from apps.accounts.models import Invitation, PendingInvite, TelegramOTP, User
from apps.billing.grants import grant_plan
from apps.billing.models import InviteReward, Plan, Reason, Subscription, SubscriptionEvent
from apps.billing.rewards import redeem_invites, reward_progress
from apps.telegrambot.models import BotMessage


def make_plans():
    Plan.objects.create(code="free", name_uz="Bepul", name_en="Free", is_default=True,
                        daily_shorts_limit=8)
    Plan.objects.create(code="plus", name_uz="Plus", name_en="Plus", price_uzs=20000,
                        daily_shorts_limit=30)
    Plan.objects.create(code="pro", name_uz="Pro", name_en="Pro", price_uzs=40000)


def make_user(n: int, **kwargs) -> User:
    return User.objects.create(
        username=f"u{n}", telegram_id=1000 + n, display_name=f"User {n}", **kwargs,
    )


class InvitationRulesTests(TestCase):
    def setUp(self):
        make_plans()
        self.inviter = make_user(1)

    def test_counted_once_even_if_called_twice(self):
        invitee = make_user(2)
        self.assertIsNotNone(register_invitation(self.inviter, invitee, "bot"))
        self.assertIsNone(register_invitation(self.inviter, invitee, "bot"))
        self.assertEqual(Invitation.objects.filter(invitee=invitee).count(), 1)

    def test_self_invite_rejected(self):
        self.assertIsNone(register_invitation(self.inviter, self.inviter, "bot"))

    def test_old_user_is_not_a_new_invite(self):
        """Eski akkaunt havolani bosib "yangi taklif" bo'lib qololmaydi."""
        old = make_user(3)
        User.objects.filter(pk=old.pk).update(date_joined=timezone.now() - timedelta(days=30))
        old.refresh_from_db()
        self.assertIsNone(register_invitation(self.inviter, old, "bot"))

    def test_pending_invite_flow(self):
        self.assertTrue(remember_pending_invite(2222, self.inviter.invite_code))
        newcomer = User.objects.create(username="new", telegram_id=2222)
        self.assertIsNotNone(attach_pending_invite(newcomer))
        # Qator ishlatilgach o'chadi
        self.assertFalse(PendingInvite.objects.filter(telegram_id=2222).exists())
        self.assertEqual(self.inviter.invited_count, 1)

    def test_pending_invite_ignored_for_existing_user(self):
        User.objects.create(username="already", telegram_id=3333)
        self.assertFalse(remember_pending_invite(3333, self.inviter.invite_code))

    def test_wrong_code_ignored(self):
        self.assertFalse(remember_pending_invite(4444, "NOSUCH"))


class NotificationCapTests(TestCase):
    """Blogger holati: kuniga 10 tadan ko'p taklifga bot xabar yubormaydi."""

    def setUp(self):
        make_plans()
        self.inviter = make_user(1)

    def test_only_first_ten_notified(self):
        for i in range(2, 2 + DAILY_NOTIFY_MAX + 5):
            register_invitation(self.inviter, make_user(i), "bot")

        invites = Invitation.objects.filter(inviter=self.inviter)
        self.assertEqual(invites.count(), DAILY_NOTIFY_MAX + 5)
        self.assertEqual(invites.filter(notified=True).count(), DAILY_NOTIFY_MAX)
        # Xabarlar orasida sovg'a tabriklari ham bor — takliflar soni aniq bo'lsin.
        invite_msgs = BotMessage.objects.filter(user=self.inviter, text__contains="taklif havolangiz")
        self.assertEqual(invite_msgs.count(), DAILY_NOTIFY_MAX)


class RewardLedgerTests(TestCase):
    def setUp(self):
        make_plans()
        self.inviter = make_user(1)

    def _invite(self, count: int, start: int = 100):
        for i in range(start, start + count):
            register_invitation(self.inviter, make_user(i), "bot")

    def test_nothing_below_threshold(self):
        self._invite(19)
        self.assertIsNone(redeem_invites(self.inviter))
        self.assertEqual(self.inviter.invites_to_next_reward, 1)
        self.assertFalse(Subscription.objects.filter(user=self.inviter).exists())

    def test_twenty_invites_give_one_month_plus(self):
        self._invite(20)
        sub = Subscription.objects.get(user=self.inviter)
        self.assertEqual(sub.plan.code, "plus")
        self.assertEqual(sub.reason, Reason.INVITE)
        self.assertEqual(self.inviter.invited_count, 20)  # jami saqlanadi
        # Hisoblagich yangi 20 likka qaytadi
        self.assertEqual(self.inviter.invites_to_next_reward, 20)

    def test_forty_invites_give_pro(self):
        self._invite(40)
        self.assertEqual(Subscription.objects.get(user=self.inviter).plan.code, "pro")

    def test_repeated_redeem_does_not_grant_again(self):
        """Eng muhim test: qayta chaqiruv yangi sovg'a bermaydi."""
        self._invite(20)
        before = Subscription.objects.get(user=self.inviter).expires_at
        for _ in range(5):
            self.assertIsNone(redeem_invites(self.inviter))
        self.assertEqual(Subscription.objects.get(user=self.inviter).expires_at, before)
        self.assertEqual(InviteReward.objects.filter(user=self.inviter).count(), 1)

    def test_second_reward_after_next_twenty(self):
        """20 → Plus, 40 → Pro. Oradagi takliflar yo'qolmaydi."""
        self._invite(30)                       # 20 da Plus berildi
        self.assertEqual(InviteReward.objects.filter(user=self.inviter).count(), 1)
        self.assertEqual(self.inviter.invites_to_next_reward, 10)
        self._invite(10, start=300)            # jami 40 → Pro
        self.assertEqual(InviteReward.objects.filter(user=self.inviter).count(), 2)
        self.assertEqual(Subscription.objects.get(user=self.inviter).plan.code, "pro")

    def test_four_hundred_invites_give_ten_pro_months(self):
        """400 ta taklif → jami Pro × 10 oy; muddat shunga qarab uzayadi."""
        self._invite(400)
        pro_months = sum(
            r.months for r in InviteReward.objects.filter(user=self.inviter, plan__code="pro")
        )
        self.assertEqual(pro_months, 10)
        sub = Subscription.objects.get(user=self.inviter)
        self.assertEqual(sub.plan.code, "pro")
        days = (sub.expires_at - timezone.now()).days
        self.assertGreaterEqual(days, 298)  # 10 × 30 kun (chetki holat uchun zaxira)

    def test_redeem_is_idempotent_at_every_total(self):
        """Har bosqichda qayta chaqirish yangi oy qo'shmaydi."""
        self._invite(60)
        before = Subscription.objects.get(user=self.inviter).expires_at
        for _ in range(10):
            self.assertIsNone(redeem_invites(self.inviter))
        self.assertEqual(Subscription.objects.get(user=self.inviter).expires_at, before)

    def test_progress_numbers(self):
        self._invite(5)
        data = reward_progress(self.inviter)
        self.assertEqual(data["invited_total"], 5)
        self.assertEqual(data["next_reward_plan"], "plus")
        self.assertEqual(data["next_reward_at"], 20)
        self.assertEqual(data["next_reward_left"], 15)


class GrantRulesTests(TestCase):
    """`grant_plan` qoidalari — pastga tushirmaslik va uzaytirish."""

    def setUp(self):
        make_plans()
        self.user = make_user(1)
        self.plus = Plan.objects.get(code="plus")
        self.pro = Plan.objects.get(code="pro")

    def test_same_plan_extends(self):
        grant_plan(self.user, self.plus, 1, Reason.PAID)
        first = Subscription.objects.get(user=self.user).expires_at
        grant_plan(self.user, self.plus, 1, Reason.PAID)
        second = Subscription.objects.get(user=self.user).expires_at
        self.assertGreater((second - first).days, 28)

    def test_upgrade_replaces(self):
        grant_plan(self.user, self.plus, 1, Reason.PAID)
        grant_plan(self.user, self.pro, 1, Reason.INVITE)
        self.assertEqual(Subscription.objects.get(user=self.user).plan.code, "pro")

    def test_downgrade_refused(self):
        grant_plan(self.user, self.pro, 1, Reason.PAID)
        self.assertIsNone(grant_plan(self.user, self.plus, 1, Reason.INVITE))
        self.assertEqual(Subscription.objects.get(user=self.user).plan.code, "pro")

    def test_invites_not_spent_when_grant_refused(self):
        """Pro'dagi odam 20 ta taklif qilsa — Plus berilmaydi, takliflar QOLADI."""
        grant_plan(self.user, self.pro, 6, Reason.PAID)
        for i in range(100, 120):
            register_invitation(self.user, make_user(i), "bot")
        self.assertEqual(InviteReward.objects.filter(user=self.user).count(), 0)
        self.assertEqual(Subscription.objects.get(user=self.user).plan.code, "pro")
        # Qarz yo'qolmaydi: 40 taga yetganda Pro sovg'asi beriladi.
        for i in range(200, 220):
            register_invitation(self.user, make_user(i), "bot")
        self.assertEqual(
            InviteReward.objects.filter(user=self.user, plan__code="pro").count(), 1,
        )

    def test_unlimited_is_not_shortened(self):
        grant_plan(self.user, self.pro, 0, Reason.TEST)   # muddatsiz
        grant_plan(self.user, self.pro, 1, Reason.INVITE)
        self.assertIsNone(Subscription.objects.get(user=self.user).expires_at)


class HistoryTests(TestCase):
    def setUp(self):
        make_plans()
        self.user = make_user(1)

    def test_every_grant_is_logged(self):
        grant_plan(self.user, Plan.objects.get(code="plus"), 1, Reason.PAID)
        grant_plan(self.user, Plan.objects.get(code="pro"), 2, Reason.INVITE)
        events = SubscriptionEvent.objects.filter(user=self.user).order_by("id")
        self.assertEqual([e.reason for e in events], [Reason.PAID, Reason.INVITE])
        self.assertEqual([e.plan.code for e in events], ["plus", "pro"])
        self.assertEqual(events[1].months, 2)

    def test_history_endpoint(self):
        from rest_framework.test import APIClient

        from apps.accounts.auth import build_tokens

        grant_plan(self.user, Plan.objects.get(code="plus"), 1, Reason.PAID)
        client = APIClient()
        tokens = build_tokens(self.user)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        res = client.get("/api/me/subscriptions/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["current"]["plan"], "plus")
        self.assertEqual(len(res.data["results"]), 1)
        self.assertEqual(res.data["results"][0]["reason"], Reason.PAID)


class OtpPurgeTests(TestCase):
    def test_expired_and_used_codes_are_deleted(self):
        now = timezone.now()
        TelegramOTP.objects.create(telegram_id=1, code="111111", expires_at=now + timedelta(seconds=60))
        TelegramOTP.objects.create(telegram_id=2, code="222222", expires_at=now - timedelta(seconds=1))
        used = TelegramOTP.objects.create(
            telegram_id=3, code="333333", is_used=True, expires_at=now + timedelta(seconds=60),
        )
        TelegramOTP.objects.filter(pk=used.pk).update(created_at=now - timedelta(minutes=5))

        TelegramOTP.purge_expired()
        self.assertEqual(list(TelegramOTP.objects.values_list("code", flat=True)), ["111111"])


class BotToSignupFlowTests(TestCase):
    """To'liq oqim: bot havolasi -> kod -> ro'yxatdan o'tish -> taklif hisobga olinadi.

    Servis darajasidagi testlar `register_invitation` ni bevosita chaqiradi;
    bu test esa `TelegramVerifyView` ichidagi ulanishni tekshiradi — ya'ni
    ro'yxatdan o'tish endpointi CHINDAN taklifni qo'llaydimi.
    """

    def setUp(self):
        make_plans()
        self.inviter = make_user(1)

    def _otp(self, telegram_id: int, code: str):
        return TelegramOTP.objects.create(
            telegram_id=telegram_id, code=code, first_name="Yangi",
            expires_at=timezone.now() + timedelta(seconds=60),
        )

    def test_new_user_via_invite_link_is_counted(self):
        from rest_framework.test import APIClient

        # 1. Bot `/start <KOD>` ni oldi
        self.assertTrue(remember_pending_invite(55555, self.inviter.invite_code))

        # 2. O'sha odam kod bilan kirdi
        self._otp(55555, "123456")
        res = APIClient().post("/api/auth/telegram/verify/", {"code": "123456"},
                               HTTP_X_PLATFORM="mobile")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["is_new"])

        # 3. Taklif hisobga olindi va taklif qilganga xabar ketdi
        self.inviter.refresh_from_db()
        self.assertEqual(self.inviter.invited_count, 1)
        self.assertEqual(BotMessage.objects.filter(user=self.inviter).count(), 1)
        self.assertFalse(PendingInvite.objects.filter(telegram_id=55555).exists())

    def test_returning_user_is_not_counted(self):
        """Ilgari ro'yxatdan o'tgan odam havolani bosib takror sanalmaydi."""
        from rest_framework.test import APIClient

        existing = User.objects.create(username="old", telegram_id=66666)
        User.objects.filter(pk=existing.pk).update(
            date_joined=timezone.now() - timedelta(days=10),
        )
        self.assertFalse(remember_pending_invite(66666, self.inviter.invite_code))

        self._otp(66666, "222333")
        APIClient().post("/api/auth/telegram/verify/", {"code": "222333"})
        self.assertEqual(self.inviter.invited_count, 0)

    def test_used_codes_are_purged_on_verify(self):
        from rest_framework.test import APIClient

        stale = self._otp(70000, "999999")
        TelegramOTP.objects.filter(pk=stale.pk).update(
            expires_at=timezone.now() - timedelta(seconds=5),
        )
        self._otp(70001, "444555")
        APIClient().post("/api/auth/telegram/verify/", {"code": "444555"})
        self.assertFalse(TelegramOTP.objects.filter(code="999999").exists())

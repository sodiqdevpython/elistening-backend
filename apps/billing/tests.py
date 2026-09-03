"""Tarif ko'tarilganda Telegram tabrigi (`apps/billing/signals.py`).

Signal `transaction.on_commit()` ishlatadi — obuna CHINDAN saqlangach xabar
yoziladi (rollback bo'lsa xabar ham ketmaydi). `TestCase` har testni
tranzaksiyaga o'rab rollback qilgani sabab `on_commit` O'ZI ishlamaydi, shu
bois `captureOnCommitCallbacks(execute=True)` ishlatiladi — aks holda testlar
"xabar yo'q" deb yolg'on yiqilardi.
"""
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.billing.models import Plan, Subscription
from apps.telegrambot.models import BotMessage


class PlanUpgradeNotifyTests(TestCase):
    def setUp(self):
        self.free = Plan.objects.create(
            code="free", name_uz="Free", name_en="Free", is_default=True, price_uzs=0,
        )
        self.plus = Plan.objects.create(
            code="plus", name_uz="Plus", name_en="Plus", price_uzs=29000,
        )
        self.pro = Plan.objects.create(
            code="pro", name_uz="Pro", name_en="Pro", price_uzs=59000,
        )
        self.user = User.objects.create(username="u1", telegram_id=12345, language="uz")

    # ── yordamchilar ──
    def commit(self, fn):
        """`fn()` ni bajarib, `on_commit` callback'larini ham ishga tushiradi."""
        with self.captureOnCommitCallbacks(execute=True):
            return fn()

    def messages(self, user=None):
        # `BotMessage.Meta.ordering` eng yangisini birinchi qo'yadi — testda
        # "oxirgi yuborilgan" kerak, shu bois tartibni ANIQ beramiz.
        return (
            BotMessage.objects
            .filter(user=user or self.user, kind=BotMessage.Kind.SYSTEM)
            .order_by("id")
        )

    # ── testlar ──
    def test_new_paid_subscription_sends_message(self):
        until = timezone.now() + timezone.timedelta(days=30)
        self.commit(lambda: Subscription.objects.create(user=self.user, plan=self.plus, expires_at=until))
        self.assertEqual(self.messages().count(), 1)
        text = self.messages().first().text
        self.assertIn("Plus", text)
        self.assertIn(timezone.localtime(until).strftime("%d.%m.%Y"), text)

    def test_free_plan_is_not_an_upgrade(self):
        self.commit(lambda: Subscription.objects.create(user=self.user, plan=self.free))
        self.assertEqual(self.messages().count(), 0)

    def test_no_telegram_id_no_message(self):
        other = User.objects.create(username="u2", telegram_id=None)
        self.commit(lambda: Subscription.objects.create(user=other, plan=self.plus))
        self.assertEqual(BotMessage.objects.filter(user=other).count(), 0)

    def test_plan_change_sends_again(self):
        sub = self.commit(lambda: Subscription.objects.create(user=self.user, plan=self.plus))
        self.assertEqual(self.messages().count(), 1)

        def upgrade():
            sub.plan = self.pro
            sub.save()

        self.commit(upgrade)
        self.assertEqual(self.messages().count(), 2)
        self.assertIn("Pro", self.messages().last().text)

    def test_saving_without_changes_does_not_resend(self):
        sub = self.commit(lambda: Subscription.objects.create(user=self.user, plan=self.plus))
        self.commit(sub.save)
        self.commit(sub.save)
        self.assertEqual(self.messages().count(), 1)

    def test_expiry_extension_sends_updated_date(self):
        sub = self.commit(lambda: Subscription.objects.create(user=self.user, plan=self.plus))
        new_until = timezone.now() + timezone.timedelta(days=60)

        def extend():
            sub.expires_at = new_until
            sub.save()

        self.commit(extend)
        self.assertEqual(self.messages().count(), 2)
        self.assertIn(timezone.localtime(new_until).strftime("%d.%m.%Y"), self.messages().last().text)

    def test_unlimited_subscription_says_so(self):
        self.commit(lambda: Subscription.objects.create(user=self.user, plan=self.pro, expires_at=None))
        self.assertIn("muddatsiz", self.messages().first().text)

    def test_english_user_gets_english_text(self):
        en_user = User.objects.create(username="u3", telegram_id=777, language="en")
        self.commit(lambda: Subscription.objects.create(user=en_user, plan=self.plus, expires_at=None))
        text = self.messages(en_user).first().text
        self.assertIn("Congratulations", text)
        self.assertIn("unlimited", text)

    def test_cancelled_subscription_is_silent(self):
        self.commit(lambda: Subscription.objects.create(
            user=self.user, plan=self.plus, status=Subscription.Status.CANCELLED,
        ))
        self.assertEqual(self.messages().count(), 0)

    def test_reactivation_sends_message(self):
        sub = self.commit(lambda: Subscription.objects.create(
            user=self.user, plan=self.plus, status=Subscription.Status.EXPIRED,
        ))
        self.assertEqual(self.messages().count(), 0)

        def reactivate():
            sub.status = Subscription.Status.ACTIVE
            sub.save()

        self.commit(reactivate)
        self.assertEqual(self.messages().count(), 1)


class LimitNotifyTests(TestCase):
    """Kunlik limitga yetilganda botga yoziladigan xabar (`limits.py`).

    Bu test bir vaqtning o'zida **modul import qilinishini** ham kafolatlaydi.
    `limits.py` faqat funksiya ichida import qilinadi (aylanma importni oldini
    olish uchun), shu bois undagi sintaksis xatosi `manage.py check` da ham,
    boshqa testlarda ham SEZILMASDAN o'tib ketardi va faqat foydalanuvchi
    limitga yetganda "portlardi". Endi sezamiz.
    """

    def setUp(self):
        self.free = Plan.objects.create(
            code="free", name_uz="Bepul", name_en="Free", is_default=True,
            daily_shorts_limit=1,
        )
        self.user = User.objects.create(username="lim", telegram_id=777, language="uz")

    def test_message_has_billing_link_and_is_sent_once_a_day(self):
        from apps.billing.limits import notify_limit_once

        notify_limit_once(self.user)
        notify_limit_once(self.user)  # o'sha kuni ikkinchi marta ketmaydi

        messages = BotMessage.objects.filter(user=self.user)
        self.assertEqual(messages.count(), 1)
        text = messages.first().text
        self.assertIn("/profile/billing", text)
        self.assertIn("Bepul", text)
        # Bot HTML rejimida yuboradi — markdown yulduzchalari bo'lmasin.
        self.assertNotIn("*", text)

    def test_no_message_without_telegram_id(self):
        from apps.billing.limits import notify_limit_once

        other = User.objects.create(username="notg")
        notify_limit_once(other)
        self.assertEqual(BotMessage.objects.filter(user=other).count(), 0)

    def test_consume_blocks_after_limit(self):
        """`consume` endi SNAPSHOT emas, TARIF qaytaradi.

        Snapshot faqat 403 javobini yasashda kerak, shu bois u
        `enforce_or_response` ga ko'chirildi — ruxsat berilgan oddiy yo'lda
        4 ta ortiqcha COUNT ketmaydi (`limits.consume` izohiga qarang).
        """
        from apps.billing.limits import consume, snapshot

        self.assertTrue(consume(self.user, "shorts", 1)[0])
        allowed, plan = consume(self.user, "shorts", 2)
        self.assertFalse(allowed)
        self.assertEqual(plan.code, "free")
        self.assertEqual(snapshot(self.user, plan)["limits"]["shorts"]["remaining"], 0)

"""Click SHOP-API testlari.

Diqqat markazida — soxta so'rov o'tib ketmasligi (imzo) va pul ikki marta
yozilmasligi (takroriy `Complete`). Qolgan tekshiruvlar Click'ning o'z
testlash dasturi (docs.click.uz/click-api-testing) birinchi bo'lib
so'raydigan holatlar.
"""
import hashlib

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.billing.models import Plan, Subscription, Wallet

from . import errors
from .models import ClickOrder

User = get_user_model()

SECRET = "click_secret_key_for_tests"
SERVICE_ID = "12345"
MERCHANT_ID = "54321"


def sign(click_trans_id, service_id, merchant_trans_id, amount, action,
         sign_time, merchant_prepare_id=""):
    """Testlar imzoni MUSTAQIL hisoblaydi — `signature.py` ni chaqirmaydi.

    Ataylab shunday: agar formula noto'g'ri o'zgartirilsa, ikkala tomon ham
    bir xil noto'g'ri hisoblab, test baribir yashil qolib ketardi.
    """
    raw = (f"{click_trans_id}{service_id}{SECRET}{merchant_trans_id}"
           f"{merchant_prepare_id if str(action) == '1' else ''}"
           f"{amount}{action}{sign_time}")
    return hashlib.md5(raw.encode()).hexdigest()


@override_settings(
    CLICK_SECRET_KEY=SECRET,
    CLICK_SERVICE_ID=SERVICE_ID,
    CLICK_MERCHANT_ID=MERCHANT_ID,
    CLICK_PAY_URL="https://my.click.uz/services/pay",
    CLICK_RETURN_URL="https://listening.uz/profile/billing",
)
class ClickBase(TestCase):
    PREPARE = "/api/click/prepare/"
    COMPLETE = "/api/click/complete/"

    def setUp(self):
        self.user = User.objects.create(username="tg55", telegram_id=55, display_name="Zafar")
        Plan.objects.create(code="free", name_uz="Oddiy", name_en="Free",
                            price_uzs=0, is_default=True)
        self.plan = Plan.objects.create(code="plus", name_uz="O'rta", name_en="Plus",
                                        price_uzs=23000)
        self.order = ClickOrder.objects.create(
            user=self.user, plan=self.plan, months=1, amount_uzs=23000,
        )

    def payload(self, action, *, order=None, amount="23000.00", click_trans_id=777001,
                sign_time="2026-09-09 10:00:00", error=0, prepare_id=None, bad_sign=False):
        order = order or self.order
        mti = str(order.pk)
        mpi = "" if action == 0 else str(order.pk if prepare_id is None else prepare_id)
        data = {
            "click_trans_id": click_trans_id,
            "service_id": SERVICE_ID,
            "click_paydoc_id": 999,
            "merchant_trans_id": mti,
            "amount": amount,
            "action": action,
            "error": error,
            "error_note": "Success" if error == 0 else "Cancelled",
            "sign_time": sign_time,
            "sign_string": "deadbeef" if bad_sign else sign(
                click_trans_id, SERVICE_ID, mti, amount, action, sign_time, mpi),
        }
        if action == 1:
            data["merchant_prepare_id"] = mpi
        return data

    def post(self, url, data):
        return self.client.post(url, data)  # form-urlencoded — Click shunday yuboradi

    def do_prepare(self, **kw):
        return self.post(self.PREPARE, self.payload(0, **kw)).json()

    def do_complete(self, **kw):
        return self.post(self.COMPLETE, self.payload(1, **kw)).json()


class SignatureTests(ClickBase):
    def test_valid_signature_passes(self):
        self.assertEqual(self.do_prepare()["error"], errors.SUCCESS)

    def test_bad_signature_rejected(self):
        self.assertEqual(self.do_prepare(bad_sign=True)["error"], errors.SIGN_CHECK_FAILED)

    def test_bad_signature_does_not_touch_order(self):
        """Imzo tekshiruvi buyurtmani qidirishdan OLDIN — holat o'zgarmaydi."""
        self.do_complete(bad_sign=True)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, ClickOrder.Status.INPUT)
        self.assertEqual(Wallet.objects.filter(user=self.user).count(), 0)

    @override_settings(CLICK_SECRET_KEY="")
    def test_missing_secret_rejects_everything(self):
        """Kalit sozlanmagan bo'lsa HECH QANDAY so'rov qabul qilinmaydi."""
        self.assertEqual(self.do_prepare()["error"], errors.SIGN_CHECK_FAILED)

    def test_amount_is_signed_as_raw_string(self):
        """Click "23000.00" yuboradi — biz uni float ga aylantirsak imzo buzilardi."""
        self.assertEqual(self.do_prepare(amount="23000.00")["error"], errors.SUCCESS)

    def test_http_status_is_always_200(self):
        response = self.post(self.PREPARE, self.payload(0, bad_sign=True))
        self.assertEqual(response.status_code, 200)


class PrepareTests(ClickBase):
    def test_prepare_marks_waiting_and_returns_pk(self):
        result = self.do_prepare()
        self.assertEqual(result["merchant_prepare_id"], self.order.pk)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, ClickOrder.Status.WAITING)
        self.assertEqual(self.order.click_trans_id, 777001)

    def test_unknown_order(self):
        ghost = ClickOrder(pk=999999, amount_uzs=23000)
        self.assertEqual(self.do_prepare(order=ghost)["error"], errors.USER_NOT_FOUND)

    def test_wrong_amount(self):
        self.assertEqual(self.do_prepare(amount="500.00")["error"], errors.INCORRECT_AMOUNT)

    def test_missing_field_is_bad_request(self):
        data = self.payload(0)
        del data["click_paydoc_id"]
        self.assertEqual(self.post(self.PREPARE, data).json()["error"], errors.BAD_REQUEST)

    def test_get_is_rejected(self):
        self.assertEqual(self.client.get(self.PREPARE).json()["error"], errors.BAD_REQUEST)


class CompleteTests(ClickBase):
    def test_payment_credits_wallet_and_activates_plan(self):
        self.do_prepare()
        result = self.do_complete()

        self.assertEqual(result["error"], errors.SUCCESS)
        self.assertEqual(result["merchant_confirm_id"], self.order.pk)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, ClickOrder.Status.CONFIRMED)
        self.assertIsNotNone(self.order.paid_at)
        # Pul kelib DARROV tarifga aylandi — balansda qoldiq yo'q.
        self.assertEqual(Wallet.objects.get(user=self.user).balance_tiyin, 0)
        self.assertTrue(Subscription.objects.filter(user=self.user, plan=self.plan).exists())

    def test_repeat_complete_is_idempotent(self):
        """Click qayta yuborsa pul IKKI MARTA yozilmaydi."""
        self.do_prepare()
        self.do_complete()
        second = self.do_complete()

        self.assertEqual(second["error"], errors.ALREADY_PAID)
        self.assertEqual(Wallet.objects.get(user=self.user).balance_tiyin, 0)
        self.assertEqual(Subscription.objects.filter(user=self.user).count(), 1)

    def test_click_error_rejects_without_touching_money(self):
        """Foydalanuvchi bekor qildi / mablag' yetmadi — pul KELMAGAN."""
        self.do_prepare()
        result = self.do_complete(error=-5017)

        self.assertEqual(result["error"], errors.TRANSACTION_CANCELLED)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, ClickOrder.Status.REJECTED)
        self.assertFalse(Wallet.objects.filter(user=self.user, balance_tiyin__gt=0).exists())
        self.assertFalse(Subscription.objects.filter(user=self.user).exists())

    def test_complete_after_rejection(self):
        self.do_prepare()
        self.do_complete(error=-5017)
        self.assertEqual(self.do_complete()["error"], errors.TRANSACTION_CANCELLED)

    def test_foreign_prepare_id_rejected(self):
        """Boshqa buyurtmaning `merchant_prepare_id` si bilan pul yozilmasin."""
        self.do_prepare()
        other = ClickOrder.objects.create(user=self.user, plan=self.plan, months=1,
                                          amount_uzs=23000)
        result = self.do_complete(prepare_id=other.pk)
        self.assertEqual(result["error"], errors.TRANSACTION_NOT_FOUND)
        self.order.refresh_from_db()
        self.assertNotEqual(self.order.status, ClickOrder.Status.CONFIRMED)

    def test_missing_prepare_id_is_bad_request(self):
        data = self.payload(1)
        del data["merchant_prepare_id"]
        self.assertEqual(self.post(self.COMPLETE, data).json()["error"], errors.BAD_REQUEST)

    def test_multi_month_order(self):
        order = ClickOrder.objects.create(user=self.user, plan=self.plan, months=3,
                                          amount_uzs=69000)
        self.do_prepare(order=order, amount="69000.00")
        result = self.do_complete(order=order, amount="69000.00")
        self.assertEqual(result["error"], errors.SUCCESS)
        self.assertTrue(Subscription.objects.filter(user=self.user, plan=self.plan).exists())
        self.assertEqual(Wallet.objects.get(user=self.user).balance_tiyin, 0)


class CheckoutApiTests(ClickBase):
    def setUp(self):
        super().setUp()
        self.api = APIClient()
        self.api.force_authenticate(user=self.user)

    def test_checkout_returns_pay_url(self):
        response = self.api.post("/api/billing/click/checkout/", {"plan": "plus", "months": 1})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["amount_uzs"], 23000)
        url = response.data["pay_url"]
        self.assertIn("my.click.uz/services/pay", url)
        self.assertIn(f"service_id={SERVICE_ID}", url)
        self.assertIn(f"merchant_id={MERCHANT_ID}", url)
        self.assertIn(f"transaction_param={response.data['order_id']}", url)
        self.assertIn("amount=23000", url)

    def test_checkout_asks_only_for_the_missing_part(self):
        """Balansda pul bo'lsa, Click TO'LIQ narxni emas, YETMAGANINI so'raydi."""
        Wallet.objects.update_or_create(user=self.user, defaults={'balance_tiyin': 1_000_000})   # 10 000 so'm
        response = self.api.post("/api/billing/click/checkout/", {"plan": "plus"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["amount_uzs"], 13000)   # 23 000 - 10 000
        self.assertEqual(response.data["price_uzs"], 23000)
        self.assertIn("amount=13000", response.data["pay_url"])

    @override_settings(CLICK_MIN_AMOUNT_UZS=1000)
    def test_missing_below_minimum_is_raised_to_minimum(self):
        """Click minimaldan kichik summani qabul qilmaydi — ko'taramiz."""
        Wallet.objects.update_or_create(user=self.user, defaults={'balance_tiyin': 2_270_000})   # 22 700 so'm
        response = self.api.post("/api/billing/click/checkout/", {"plan": "plus"})
        self.assertEqual(response.data["amount_uzs"], 1000)   # 300 emas
        self.assertEqual(response.data["extra_uzs"], 700)     # ortiqchasi hamyonda qoladi

    def test_checkout_refused_when_balance_is_enough(self):
        """Pul yetsa to'lov shart emas — hisobdan yoqiladi."""
        Wallet.objects.update_or_create(user=self.user, defaults={'balance_tiyin': 2_300_000})
        response = self.api.post("/api/billing/click/checkout/", {"plan": "plus"})
        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.data["enough"])

    def test_existing_order_amount_is_refreshed(self):
        """Balans o'zgargach eski buyurtma ESKI summa bilan qolib ketmasin."""
        first = self.api.post("/api/billing/click/checkout/", {"plan": "plus"})
        self.assertEqual(first.data["amount_uzs"], 23000)

        Wallet.objects.update_or_create(user=self.user, defaults={'balance_tiyin': 1_000_000})
        second = self.api.post("/api/billing/click/checkout/", {"plan": "plus"})
        self.assertEqual(second.data["order_id"], first.data["order_id"])
        self.assertEqual(second.data["amount_uzs"], 13000)

    def test_higher_plan_gets_its_own_order(self):
        """Yuqori tarifga o'tmoqchi bo'lsa — alohida buyurtma, to'g'ri summa."""
        Plan.objects.create(code="pro", name_uz="Yuqori", name_en="Pro", price_uzs=32000)
        Wallet.objects.update_or_create(user=self.user, defaults={'balance_tiyin': 1_000_000})
        plus = self.api.post("/api/billing/click/checkout/", {"plan": "plus"})
        pro = self.api.post("/api/billing/click/checkout/", {"plan": "pro"})
        self.assertNotEqual(plus.data["order_id"], pro.data["order_id"])
        self.assertEqual(pro.data["amount_uzs"], 22000)   # 32 000 - 10 000

    def test_checkout_reuses_unpaid_order(self):
        """Tugma ikki marta bosilsa ikkita "osilgan" buyurtma qolmaydi."""
        first = self.api.post("/api/billing/click/checkout/", {"plan": "plus"})
        second = self.api.post("/api/billing/click/checkout/", {"plan": "plus"})
        self.assertEqual(first.data["order_id"], second.data["order_id"])

    def test_checkout_months_price(self):
        response = self.api.post("/api/billing/click/checkout/", {"plan": "plus", "months": 3})
        self.assertEqual(response.data["amount_uzs"], 69000)

    def test_free_plan_rejected(self):
        response = self.api.post("/api/billing/click/checkout/", {"plan": "free"})
        self.assertEqual(response.status_code, 400)

    @override_settings(CLICK_SERVICE_ID="", CLICK_SECRET_KEY="")
    def test_unconfigured_click_is_503(self):
        response = self.api.post("/api/billing/click/checkout/", {"plan": "plus"})
        self.assertEqual(response.status_code, 503)

    @override_settings(CLICK_MERCHANT_ID="")
    def test_works_without_merchant_id(self):
        """`merchant_id` kabinetda YO'Q va Click'ning o'z kutubxonasida ham
        ishlatilmaydi — usiz ham havola yasalishi va tugma ishlashi kerak."""
        response = self.api.post("/api/billing/click/checkout/", {"plan": "plus"})
        self.assertEqual(response.status_code, 200)
        url = response.data["pay_url"]
        self.assertNotIn("merchant_id", url)
        self.assertIn(f"service_id={SERVICE_ID}", url)

    @override_settings(CLICK_MERCHANT_ID="")
    def test_click_button_visible_without_merchant_id(self):
        response = self.api.get("/api/billing/wallet/")
        self.assertTrue(response.data["providers"]["click"]["enabled"])

    def test_order_status_reflects_payment(self):
        created = self.api.post("/api/billing/click/checkout/", {"plan": "plus"})
        order_id = created.data["order_id"]
        order = ClickOrder.objects.get(pk=order_id)

        status_url = f"/api/billing/click/orders/{order_id}/"
        self.assertFalse(self.api.get(status_url).data["paid"])

        self.do_prepare(order=order)
        self.do_complete(order=order)
        self.assertTrue(self.api.get(status_url).data["paid"])

    def test_other_users_order_is_hidden(self):
        stranger = User.objects.create(username="tg56", telegram_id=56)
        client = APIClient()
        client.force_authenticate(user=stranger)
        response = client.get(f"/api/billing/click/orders/{self.order.pk}/")
        self.assertEqual(response.status_code, 404)

    def test_anonymous_rejected(self):
        response = APIClient().post("/api/billing/click/checkout/", {"plan": "plus"})
        self.assertEqual(response.status_code, 401)

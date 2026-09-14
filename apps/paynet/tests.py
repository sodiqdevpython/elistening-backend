"""Paynet integratsiyasi testlari.

Diqqat markazida — PUL yo'qolmasligi va IKKI MARTA yozilmasligi:
takroriy so'rov, bir vaqtdagi bekor qilish, sarflangan balansni qaytarish.
Qolgan tekshiruvlar (format, xato kodlari) Paynet integratsiya testida
birinchi bo'lib so'raladigan holatlar.
"""
import base64
import json

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.billing.models import Plan, Subscription, Wallet

from . import errors
from .models import PaynetTransaction

User = get_user_model()

LOGIN, PASSWORD = "paynet_user", "paynet_secret_pw"
GOOD_IP = "213.230.106.113"   # 213.230.106.112/28 ichida
BAD_IP = "8.8.8.8"

AUTH = "Basic " + base64.b64encode(f"{LOGIN}:{PASSWORD}".encode()).decode()


@override_settings(
    PAYNET_LOGIN=LOGIN,
    PAYNET_PASSWORD=PASSWORD,
    PAYNET_ALLOWED_NETS=["213.230.106.112/28", "213.230.65.80/28"],
    PAYNET_SERVICE_IDS=[],
    PAYNET_CLIENT_FIELD="client_id",
)
class PaynetBase(TestCase):
    URL = "/api/paynet/"

    def setUp(self):
        self.user = User.objects.create(username="tg777", telegram_id=777, display_name="Ali")

    def call(self, method, params=None, *, ip=GOOD_IP, auth=AUTH, rid=1, raw=None):
        body = raw if raw is not None else json.dumps(
            {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}
        )
        extra = {"HTTP_X_REAL_IP": ip}
        if auth is not None:
            extra["HTTP_AUTHORIZATION"] = auth
        return self.client.post(self.URL, data=body,
                                content_type="application/json", **extra)

    def result(self, *args, **kwargs):
        response = self.call(*args, **kwargs)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertNotIn("error", payload, msg=payload)
        return payload["result"]

    def error_code(self, *args, **kwargs):
        response = self.call(*args, **kwargs)
        payload = response.json()
        self.assertIn("error", payload, msg=payload)
        return payload["error"]["code"]

    def pay(self, trn_id=1001, amount=2_300_000, client=777, service=1):
        return self.result("PerformTransaction", {
            "amount": amount, "serviceId": service, "transactionId": trn_id,
            "fields": {"client_id": str(client)},
        })


class SecurityTests(PaynetBase):
    def test_unknown_ip_is_denied(self):
        self.assertEqual(self.error_code("GetInformation", ip=BAD_IP), errors.ACCESS_DENIED)

    def test_ip_check_runs_before_auth(self):
        """Notanish IP'ga parol to'g'rimi-yo'qligi haqida ma'lumot bermaymiz."""
        response = self.call("GetInformation", ip=BAD_IP, auth=None)
        self.assertEqual(response.status_code, 200)  # 401 EMAS
        self.assertEqual(response.json()["error"]["code"], errors.ACCESS_DENIED)

    def test_missing_auth_is_401(self):
        response = self.call("GetInformation", auth=None)
        self.assertEqual(response.status_code, 401)
        self.assertIn("WWW-Authenticate", response)

    def test_wrong_password_is_401(self):
        bad = "Basic " + base64.b64encode(f"{LOGIN}:nope".encode()).decode()
        self.assertEqual(self.call("GetInformation", auth=bad).status_code, 401)

    @override_settings(PAYNET_ALLOWED_NETS=[], DEBUG=False)
    def test_empty_allowlist_blocks_everyone_in_prod(self):
        """Sozlamani unutib qoldirish "hammaga ochiq" ga AYLANMAYDI."""
        self.assertEqual(self.error_code("GetInformation"), errors.ACCESS_DENIED)

    def test_get_is_rejected_with_32300(self):
        response = self.client.get(self.URL, HTTP_X_REAL_IP=GOOD_IP, HTTP_AUTHORIZATION=AUTH)
        self.assertEqual(response.json()["error"]["code"], errors.NOT_POST)

    @override_settings(PAYNET_SERVICE_IDS=["7"])
    def test_foreign_service_id_rejected(self):
        code = self.error_code("GetInformation", {"serviceId": 9, "fields": {"client_id": "777"}})
        self.assertEqual(code, errors.SERVICE_NOT_FOUND)


class ProtocolTests(PaynetBase):
    def test_broken_json(self):
        self.assertEqual(self.error_code("x", raw="{not json"), errors.PARSE_ERROR)

    def test_unknown_method(self):
        self.assertEqual(self.error_code("Nonexistent"), errors.METHOD_NOT_FOUND)

    def test_missing_jsonrpc_version(self):
        raw = json.dumps({"id": 5, "method": "GetInformation", "params": {}})
        self.assertEqual(self.error_code("x", raw=raw), errors.INVALID_REQUEST)

    def test_id_is_echoed_back_on_error(self):
        response = self.call("Nonexistent", rid=98765)
        self.assertEqual(response.json()["id"], 98765)

    def test_method_name_with_stray_space(self):
        """Hujjat misollarining o'zida `" CancelTransaction"` deb yozilgan."""
        raw = json.dumps({"jsonrpc": "2.0", "id": 1, "method": " GetInformation",
                          "params": {"serviceId": 1, "fields": {"client_id": "777"}}})
        self.assertEqual(self.call("x", raw=raw).json()["result"]["status"], "0")


class GetInformationTests(PaynetBase):
    def test_known_client(self):
        Wallet.objects.create(user=self.user, balance_tiyin=2_300_000)   # 23 000 so'm
        result = self.result("GetInformation", {"serviceId": 1, "fields": {"client_id": "777"}})
        # Paynet talabi (11.09.2026): `status` SATR, balans SO'MDA.
        self.assertEqual(result["status"], "0")
        self.assertIsInstance(result["status"], str)
        self.assertEqual(result["fields"], {"name": "Ali", "balance": 23000})

    def test_unknown_client(self):
        code = self.error_code("GetInformation", {"serviceId": 1, "fields": {"client_id": "404404"}})
        self.assertEqual(code, errors.CLIENT_NOT_FOUND)

    def test_non_numeric_client_id(self):
        code = self.error_code("GetInformation", {"serviceId": 1, "fields": {"client_id": "abc"}})
        self.assertEqual(code, errors.INVALID_PARAM_1)

    def test_missing_client_id(self):
        code = self.error_code("GetInformation", {"serviceId": 1, "fields": {}})
        self.assertEqual(code, errors.MISSING_PARAMS)

    def test_auto_username_is_not_shown_as_name(self):
        """Ism yo'q, username avtomatik `tg...` — kassada ID chiqadi."""
        bare = User.objects.create(username="tg888", telegram_id=888, display_name="")
        result = self.result("GetInformation", {"serviceId": 1, "fields": {"client_id": "888"}})
        self.assertEqual(result["fields"]["name"], "ID 888")
        self.assertEqual(set(result["fields"]), {"name", "balance"})   # Таблица 5
        bare.delete()

    def test_inactive_user_is_forbidden(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        code = self.error_code("GetInformation", {"serviceId": 1, "fields": {"client_id": "777"}})
        self.assertEqual(code, errors.TRN_FORBIDDEN)


class PerformTransactionTests(PaynetBase):
    def test_payment_credits_wallet(self):
        result = self.pay(amount=2_300_000)
        self.assertEqual(Wallet.objects.get(user=self.user).balance_tiyin, 2_300_000)
        # Javobdagi balans SO'MDA (Paynet talabi), bazada esa tiyinda.
        self.assertEqual(result["fields"]["balance"], 23000)
        # Таблица 4 da e'lon qilingan tarkib — hujjat bilan AYNAN mos.
        self.assertEqual(set(result["fields"]), {"client_id", "name", "balance"})
        self.assertEqual(result["fields"]["name"], "Ali")
        self.assertEqual(result["providerTrnId"], PaynetTransaction.objects.get().pk)

    def test_repeat_returns_201(self):
        """Bir xil `transactionId` bilan takror kelsa — 201 (Paynet talabi).

        Pul baribir ikki marta yozilmaydi: buni javob kodi emas,
        `transaction_id` ustidagi UNIQUE indeks kafolatlaydi.
        """
        self.pay(trn_id=555)
        code = self.error_code("PerformTransaction", {
            "amount": 2_300_000, "serviceId": 1, "transactionId": 555,
            "fields": {"client_id": "777"},
        })
        self.assertEqual(code, errors.TRN_EXISTS)
        self.assertEqual(PaynetTransaction.objects.count(), 1)
        self.assertEqual(Wallet.objects.get(user=self.user).balance_tiyin, 2_300_000)

    def test_same_id_different_amount_is_conflict(self):
        self.pay(trn_id=556, amount=2_300_000)
        code = self.error_code("PerformTransaction", {
            "amount": 999_999, "serviceId": 1, "transactionId": 556,
            "fields": {"client_id": "777"},
        })
        self.assertEqual(code, errors.TRN_EXISTS)

    def test_unknown_client_is_not_charged(self):
        code = self.error_code("PerformTransaction", {
            "amount": 2_300_000, "serviceId": 1, "transactionId": 557,
            "fields": {"client_id": "999999"},
        })
        self.assertEqual(code, errors.CLIENT_NOT_FOUND)
        self.assertFalse(PaynetTransaction.objects.exists())

    def test_amount_below_minimum(self):
        code = self.error_code("PerformTransaction", {
            "amount": 10, "serviceId": 1, "transactionId": 558, "fields": {"client_id": "777"},
        })
        self.assertEqual(code, errors.BAD_AMOUNT)

    def test_amount_above_maximum(self):
        code = self.error_code("PerformTransaction", {
            "amount": 999_999_999_999, "serviceId": 1, "transactionId": 559,
            "fields": {"client_id": "777"},
        })
        self.assertEqual(code, errors.AMOUNT_TOO_BIG)

    def test_fractional_amount_rejected(self):
        code = self.error_code("PerformTransaction", {
            "amount": 100_000.5, "serviceId": 1, "transactionId": 560,
            "fields": {"client_id": "777"},
        })
        self.assertEqual(code, errors.BAD_AMOUNT)

    def test_timestamp_format(self):
        result = self.pay(trn_id=561)
        # "YYYY-MM-dd HH:mm:ss" — UWS_JSON 2.2
        timezone.datetime.strptime(result["timestamp"], "%Y-%m-%d %H:%M:%S")


class CheckAndCancelTests(PaynetBase):
    def test_check_known(self):
        self.pay(trn_id=700)
        result = self.result("CheckTransaction", {"serviceId": 1, "transactionId": 700,
                                                  "timestamp": "2025-01-01 10:00:00"})
        self.assertEqual(result["transactionState"], PaynetTransaction.State.SUCCESS)

    def test_check_unknown_returns_state_3(self):
        result = self.result("CheckTransaction", {"serviceId": 1, "transactionId": 9})
        self.assertEqual(result["transactionState"], PaynetTransaction.State.NOT_FOUND)
        self.assertEqual(result["providerTrnId"], 0)

    def test_cancel_refunds_balance(self):
        self.pay(trn_id=701, amount=2_300_000)
        result = self.result("CancelTransaction", {"serviceId": 1, "transactionId": 701,
                                                   "timestamp": "2025-01-01 10:00:00"})
        self.assertEqual(result["transactionState"], PaynetTransaction.State.CANCELLED)
        self.assertEqual(Wallet.objects.get(user=self.user).balance_tiyin, 0)

    def test_second_cancel_is_idempotent(self):
        self.pay(trn_id=702)
        self.result("CancelTransaction", {"serviceId": 1, "transactionId": 702})
        result = self.result("CancelTransaction", {"serviceId": 1, "transactionId": 702})
        self.assertEqual(result["transactionState"], PaynetTransaction.State.CANCELLED)
        self.assertEqual(Wallet.objects.get(user=self.user).balance_tiyin, 0)

    def test_cancel_after_spending_returns_77(self):
        """Pul tarifga sarflangan — hujjat aynan shu holat uchun 77 ni ajratgan."""
        plan = Plan.objects.create(code="plus", name_uz="O'rta", name_en="Plus", price_uzs=23000)
        self.pay(trn_id=703, amount=2_300_000)
        from apps.billing.wallet import buy_plan
        event, _ = buy_plan(self.user, plan, 1)
        self.assertIsNotNone(event)

        code = self.error_code("CancelTransaction", {"serviceId": 1, "transactionId": 703})
        self.assertEqual(code, errors.NOT_ENOUGH_TO_CANCEL)
        self.assertEqual(PaynetTransaction.objects.get().state, PaynetTransaction.State.SUCCESS)

    def test_cancel_accepts_dotted_date_from_the_docs(self):
        """Hujjatning O'ZI `CancelTransaction` misolida `16.06.2021 12:44:57` beradi."""
        self.pay(trn_id=704)
        result = self.result("CancelTransaction", {"serviceId": 1, "transactionId": 704,
                                                   "timestamp": "16.06.2021 12:44:57"})
        self.assertEqual(result["transactionState"], PaynetTransaction.State.CANCELLED)

    def test_bad_date_format(self):
        self.pay(trn_id=705)
        code = self.error_code("CancelTransaction", {"serviceId": 1, "transactionId": 705,
                                                     "timestamp": "yesterday"})
        self.assertEqual(code, errors.BAD_DATETIME)


class GetStatementTests(PaynetBase):
    def test_only_successful_in_period(self):
        self.pay(trn_id=801, amount=1_000_000)
        self.pay(trn_id=802, amount=2_000_000)
        self.result("CancelTransaction", {"serviceId": 1, "transactionId": 802})

        today = timezone.localtime().strftime("%Y-%m-%d")
        result = self.result("GetStatement", {
            "serviceId": 1, "dateFrom": f"{today} 00:00:00", "dateTo": f"{today} 23:59:59",
        })
        self.assertEqual([s["transactionId"] for s in result["statements"]], [801])
        self.assertEqual(result["statements"][0]["amount"], 1_000_000)
        # Paynet talabi: `transactionId` SON bo'lishi shart (satr emas).
        row = result["statements"][0]
        for field in ("transactionId", "providerTrnId", "amount"):
            self.assertIsInstance(row[field], int, msg=f"{field} int bo'lishi kerak")

    def test_reversed_period_is_rejected(self):
        code = self.error_code("GetStatement", {
            "serviceId": 1, "dateFrom": "2025-02-01 00:00:00", "dateTo": "2025-01-01 00:00:00",
        })
        self.assertEqual(code, errors.BAD_DATETIME)


class ChangePasswordTests(PaynetBase):
    def test_password_change_takes_effect(self):
        self.assertEqual(self.result("ChangePassword", {"newPassword": "brand-new-pw-1"}), "success")

        # Eski parol endi ishlamaydi...
        self.assertEqual(self.call("GetInformation").status_code, 401)
        # ...yangisi ishlaydi.
        new_auth = "Basic " + base64.b64encode(f"{LOGIN}:brand-new-pw-1".encode()).decode()
        response = self.call("GetInformation", {"serviceId": 1, "fields": {"client_id": "777"}},
                             auth=new_auth)
        self.assertEqual(response.status_code, 200)

    def test_short_password_rejected(self):
        self.assertEqual(self.error_code("ChangePassword", {"newPassword": "x"}),
                         errors.MISSING_PARAMS)


class PendingPlanTests(PaynetBase):
    """Saytda tarif tanlab, keyin Paynet'da to'lash — tarif AVTOMATIK yoqiladi."""

    def setUp(self):
        super().setUp()
        Plan.objects.create(code="free", name_uz="Oddiy", name_en="Free", price_uzs=0,
                            is_default=True)
        self.plan = Plan.objects.create(code="plus", name_uz="O'rta", name_en="Plus",
                                        price_uzs=23000)

    def test_payment_activates_pending_plan(self):
        Wallet.objects.create(user=self.user, pending_plan=self.plan, pending_months=1)
        result = self.pay(trn_id=901, amount=2_300_000)

        sub = Subscription.objects.get(user=self.user)
        self.assertEqual(sub.plan, self.plan)
        wallet = Wallet.objects.get(user=self.user)
        self.assertEqual(wallet.balance_tiyin, 0)
        self.assertIsNone(wallet.pending_plan)
        # Chekdagi balans HAQIQIY qoldiqni ko'rsatadi (tarif yechilgandan keyin).
        self.assertEqual(result["fields"]["balance"], 0)

    def test_partial_payment_waits(self):
        Wallet.objects.create(user=self.user, pending_plan=self.plan, pending_months=1)
        self.pay(trn_id=902, amount=1_000_000)

        self.assertFalse(Subscription.objects.filter(user=self.user).exists())
        wallet = Wallet.objects.get(user=self.user)
        self.assertEqual(wallet.balance_tiyin, 1_000_000)
        self.assertEqual(wallet.pending_plan, self.plan)

        # Qolgani kelgach — yoqiladi, ortiqcha pul balansda qoladi.
        self.pay(trn_id=903, amount=1_500_000)
        self.assertTrue(Subscription.objects.filter(user=self.user).exists())
        self.assertEqual(Wallet.objects.get(user=self.user).balance_tiyin, 200_000)

    def test_payment_without_intent_stays_in_balance(self):
        self.pay(trn_id=904, amount=2_300_000)
        self.assertFalse(Subscription.objects.filter(user=self.user).exists())
        self.assertEqual(Wallet.objects.get(user=self.user).balance_tiyin, 2_300_000)

"""Admin "📊 To'lovlar dashboard" — hisob-kitob va kirish huquqi."""
from datetime import date, datetime, timezone as dt_timezone

from django.contrib.auth import get_user_model
from django.test import TestCase

from .dashboard import dashboard_data
from .models import WalletTransaction

User = get_user_model()
Kind = WalletTransaction.Kind
P = WalletTransaction.Provider


def utc(y, m, d, h=12, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=dt_timezone.utc)


class DashboardDataTests(TestCase):
    def setUp(self):
        self.ali = User.objects.create(username="tg1", telegram_id=1, display_name="Ali")
        self.vali = User.objects.create(username="vali_uz", telegram_id=2, display_name="Vali")

    def tx(self, user, provider, uzs, when, kind=Kind.DEPOSIT, ext=""):
        return WalletTransaction.objects.create(
            user=user, kind=kind, provider=provider, amount_tiyin=uzs * 100,
            occurred_at=when, external_id=ext,
        )

    def data(self, **params):
        base = {"from": "2026-10-01", "to": "2026-10-05"}
        return dashboard_data({**base, **params})

    def test_series_fills_gaps_and_nets_refunds(self):
        self.tx(self.ali, P.PAYNET, 50000, utc(2026, 10, 1))
        self.tx(self.vali, P.CLICK, 23000, utc(2026, 10, 1))
        self.tx(self.ali, P.PAYNET, 10000, utc(2026, 10, 3))
        self.tx(self.ali, P.PAYNET, -10000, utc(2026, 10, 3, 13), kind=Kind.REFUND)
        self.tx(self.ali, P.WALLET, -23000, utc(2026, 10, 3), kind=Kind.PURCHASE)  # tushum emas

        d = self.data()
        days = {row["bucket"]: row for row in d["series"]}
        self.assertEqual(len(d["series"]), 5)  # bo'sh kunlar ham 0 bilan
        self.assertEqual((days["2026-10-01"]["paynet"], days["2026-10-01"]["click"],
                          days["2026-10-01"]["total"]), (50000, 23000, 73000))
        self.assertEqual(days["2026-10-03"]["total"], 0)  # tushdi va bekor qilindi
        self.assertEqual(days["2026-10-02"]["total"], 0)
        k = d["kpis"]
        self.assertEqual((k["net_uzs"], k["deposits_uzs"], k["deposits_count"]), (73000, 83000, 3))
        self.assertEqual((k["payers"], k["refunds_count"], k["refunds_uzs"]), (2, 1, 10000))

    def test_days_follow_tashkent_time(self):
        # 11-oktabr 20:30 UTC = 12-oktabr 01:30 Toshkent.
        self.tx(self.ali, P.CLICK, 1000, utc(2026, 10, 11, 20, 30))
        d = dashboard_data({"from": "2026-10-11", "to": "2026-10-12"})
        self.assertEqual([(r["bucket"], r["total"]) for r in d["series"]],
                         [("2026-10-11", 0), ("2026-10-12", 1000)])

    def test_week_and_month_buckets(self):
        self.tx(self.ali, P.PAYNET, 1000, utc(2026, 9, 30))
        self.tx(self.ali, P.PAYNET, 2000, utc(2026, 10, 2))
        weeks = dashboard_data({"from": "2026-09-28", "to": "2026-10-04", "granularity": "week"})
        self.assertEqual([(r["bucket"], r["total"]) for r in weeks["series"]], [("2026-09-28", 3000)])
        months = dashboard_data({"from": "2026-09-01", "to": "2026-10-31", "granularity": "month"})
        self.assertEqual([(r["bucket"], r["total"]) for r in months["series"]],
                         [("2026-09-01", 1000), ("2026-10-01", 2000)])

    def test_provider_filter_keeps_share_complete(self):
        self.tx(self.ali, P.PAYNET, 30000, utc(2026, 10, 2))
        self.tx(self.vali, P.CLICK, 10000, utc(2026, 10, 2))
        d = self.data(provider="click")
        self.assertEqual(d["kpis"]["net_uzs"], 10000)
        self.assertEqual({s["provider"]: s["uzs"] for s in d["share"]}, {"paynet": 30000, "click": 10000})
        self.assertEqual([r["provider"] for r in d["table"]["rows"]], ["click"])

    def test_clicked_point_filters_table(self):
        self.tx(self.ali, P.PAYNET, 1000, utc(2026, 10, 1), ext="901")
        self.tx(self.vali, P.CLICK, 2000, utc(2026, 10, 4))
        t = self.data(bucket="2026-10-04")["table"]
        self.assertEqual((t["bucket"], t["count"], t["total_uzs"]), ("2026-10-04", 1, 2000))
        row = t["rows"][0]
        self.assertEqual((row["user"]["name"], row["provider_label"], row["amount_uzs"]), ("Vali", "Click", 2000))
        self.assertEqual(row["user"]["admin_url"], f"/admin/accounts/user/{self.vali.pk}/change/")

    def test_search_by_name_telegram_id_and_transaction(self):
        self.tx(self.ali, P.PAYNET, 1000, utc(2026, 10, 1), ext="555")
        self.tx(self.vali, P.CLICK, 2000, utc(2026, 10, 2))
        names = lambda **p: [r["user"]["name"] for r in self.data(**p)["table"]["rows"]]  # noqa: E731
        self.assertEqual(names(q="vali"), ["Vali"])
        self.assertEqual(names(q="1"), ["Ali"])        # Telegram ID
        self.assertEqual(names(q="555"), ["Ali"])      # tranzaksiya ID

    def test_bad_params_fall_back_safely(self):
        d = dashboard_data({"from": "nonsense", "to": "2026-10-05", "granularity": "year", "provider": "x"})
        self.assertEqual((d["filters"]["granularity"], d["filters"]["provider"]), ("day", ""))
        self.assertEqual(d["filters"]["to"], "2026-10-05")
        swapped = dashboard_data({"from": "2026-10-05", "to": "2026-10-01"})["filters"]
        self.assertEqual((swapped["from"], swapped["to"]), ("2026-10-01", "2026-10-05"))

    def test_all_starts_from_first_payment(self):
        self.tx(self.ali, P.PAYNET, 1000, utc(2026, 8, 15))
        d = dashboard_data({"from": "all", "to": "2026-10-05", "granularity": "month"})
        self.assertEqual(d["filters"]["from"], date(2026, 8, 15).isoformat())
        self.assertEqual(d["series"][0]["bucket"], "2026-08-01")


class DashboardAccessTests(TestCase):
    URL = "/admin/billing/paymentsdashboard/"

    def test_anonymous_and_regular_users_are_sent_to_login(self):
        for path in (self.URL, self.URL + "data/"):
            self.assertEqual(self.client.get(path).status_code, 302)
        User.objects.create_user(username="plain", password="pw12345!x")
        self.client.login(username="plain", password="pw12345!x")
        self.assertEqual(self.client.get(self.URL + "data/").status_code, 302)

    def test_staff_without_permission_is_forbidden(self):
        User.objects.create_user(username="staff", password="pw12345!x", is_staff=True)
        self.client.login(username="staff", password="pw12345!x")
        self.assertEqual(self.client.get(self.URL + "data/").status_code, 403)

    def test_superuser_sees_page_and_data(self):
        boss = User.objects.create_superuser(username="boss", password="pw12345!x")
        self.client.force_login(boss)
        page = self.client.get(self.URL)
        self.assertContains(page, "To'lovlar dashboard")
        self.assertContains(page, "chart.js@4.4.1")
        data = self.client.get(self.URL + "data/?from=2026-10-01&to=2026-10-02").json()
        self.assertEqual(set(data), {"filters", "kpis", "share", "series", "table"})
        index = self.client.get("/admin/")
        self.assertContains(index, self.URL)  # menyuda alohida bo'lim

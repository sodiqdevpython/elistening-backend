"""Sayt login captcha'si (Cloudflare Turnstile) — `captcha.py` + `/auth/telegram/verify/`."""
import json
import urllib.parse
from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from .captcha import captcha_enabled, verify_captcha
from .models import TelegramOTP

KEYS = {"TURNSTILE_SECRET": "secret-x", "TURNSTILE_SITEKEY": "site-x"}
VERIFY = "/api/auth/telegram/verify/"


def cloudflare_says(success: bool):
    """`urlopen` o'rniga — Cloudflare javobini soxtalaydi va so'rovni eslab qoladi."""
    resp = mock.MagicMock()
    resp.read.return_value = json.dumps({"success": success}).encode()
    resp.__enter__.return_value = resp
    return mock.patch("apps.accounts.captcha.urllib.request.urlopen", return_value=resp)


class CaptchaSwitchTests(TestCase):
    def test_off_without_both_keys(self):
        for keys in ({}, {"TURNSTILE_SECRET": "s"}, {"TURNSTILE_SITEKEY": "k"}):
            with self.subTest(keys=keys), override_settings(**{"TURNSTILE_SECRET": "", "TURNSTILE_SITEKEY": "", **keys}):
                self.assertFalse(captcha_enabled())
                self.assertTrue(verify_captcha(""))  # o'chiq — login to'silmaydi
                self.assertEqual(self.client.get("/api/config/").json()["captcha_sitekey"], "")

    @override_settings(**KEYS)
    def test_sitekey_reaches_the_site(self):
        self.assertEqual(self.client.get("/api/config/").json()["captcha_sitekey"], "site-x")

    @override_settings(**KEYS)
    def test_cloudflare_is_asked_with_secret_token_and_ip(self):
        with cloudflare_says(True) as urlopen:
            self.assertTrue(verify_captcha("tok", "1.2.3.4"))
        req = urlopen.call_args.args[0]
        self.assertEqual(req.full_url, "https://challenges.cloudflare.com/turnstile/v0/siteverify")
        self.assertEqual(urllib.parse.parse_qs(req.data.decode()),
                         {"secret": ["secret-x"], "response": ["tok"], "remoteip": ["1.2.3.4"]})
        with cloudflare_says(False):
            self.assertFalse(verify_captcha("tok"))

    @override_settings(**KEYS)
    def test_network_error_fails_closed(self):
        with mock.patch("apps.accounts.captcha.urllib.request.urlopen", side_effect=OSError("down")):
            self.assertFalse(verify_captcha("tok"))


@override_settings(**KEYS)
class VerifyEndpointTests(TestCase):
    def setUp(self):
        TelegramOTP.objects.create(telegram_id=42, code="482913", first_name="Ali",
                                   expires_at=timezone.now() + timedelta(seconds=60))

    def post(self, **extra):
        return self.client.post(VERIFY, {"code": "482913", **extra.pop("data", {})}, **extra)

    def test_web_without_token_is_rejected(self):
        with cloudflare_says(True) as urlopen:
            r = self.post()
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"]["code"], "captcha_failed")
        urlopen.assert_not_called()  # bo'sh token Cloudflare'ga yuborilmaydi
        self.assertFalse(TelegramOTP.objects.get(code="482913").is_used)

    def test_web_with_solved_captcha_logs_in(self):
        with cloudflare_says(True):
            r = self.post(data={"captcha_token": "tok"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("access", r.json())

    def test_mobile_skips_captcha(self):
        with cloudflare_says(False) as urlopen:
            r = self.post(HTTP_X_PLATFORM="mobile")
        self.assertEqual(r.status_code, 200)
        urlopen.assert_not_called()

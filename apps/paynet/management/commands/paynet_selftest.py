"""Paynet integratsiyasini O'ZIMIZ sinash — ular test qilishidan OLDIN.

    python manage.py paynet_selftest --client-id 123456789

## Nega kerak

Paynet'ga ma'lumot berganimizdan keyin ular test qiladi va biror narsa
noto'g'ri bo'lsa yozishmalar cho'ziladi. Bu buyruq AYNAN Paynet yuboradigan
so'rovlarni yuboradi va javobni ko'rsatadi — muammoni oldindan topamiz.

## Nega `curl` emas

Tashqaridan `curl` bilan sinab bo'lmaydi: nginx ham, Django ham IP ro'yxatini
tekshiradi va sizning manzilingiz Paynet ro'yxatida yo'q (`403` / `601`).
Bu buyruq Django'ning test klientidan foydalanadi — u tarmoqdan o'tmaydi,
lekin **haqiqiy kod**, **haqiqiy sozlama** va **haqiqiy bazani** ishlatadi.
Ya'ni natija chinakam, soxta emas.

## Xavfsizlik

Sukut bo'yicha faqat **o'qish** testlari (`GetInformation`, xato holatlari) —
bazaga hech narsa yozilmaydi.

`--full` bilan pul yo'li ham sinaladi: kichik summa yoziladi, holati
tekshiriladi va **darrov bekor qilinadi**. Oxirida balans boshlang'ich
holatiga qaytadi; buyruq buni tekshirib ham ko'rsatadi.
"""
import base64
import json
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.test import Client

from apps.billing.models import Wallet
from apps.paynet.models import PaynetTransaction

#: Ro'yxatdagi haqiqiy Paynet manzili — IP tekshiruvi o'tsin.
PAYNET_IP = "213.230.106.113"
#: Ro'yxatda YO'Q manzil — "begona IP rad etiladimi" testi uchun.
FOREIGN_IP = "8.8.8.8"


class Command(BaseCommand):
    help = "Paynet JSON-RPC servisini ichkaridan sinaydi (Paynet nima qilsa, shuni)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--client-id", required=True,
            help="Telegram chat ID — Paynet'ga beradigan `Field Value`.",
        )
        parser.add_argument(
            "--full", action="store_true",
            help="To'lov yo'lini ham sinaydi (yoziladi va darrov bekor qilinadi).",
        )

    # ── Yordamchilar ─────────────────────────────────────────────────────
    def _call(self, method, params, *, ip=PAYNET_IP, auth=True, password=None):
        """Paynet yuboradigan so'rovni aynan takrorlaydi."""
        body = json.dumps({"jsonrpc": "2.0", "id": int(time.time()),
                           "method": method, "params": params})
        extra = {
            "HTTP_X_REAL_IP": ip,
            # `SERVER_NAME` — ALLOWED_HOSTS tekshiruvi uchun. Bu yerda xato
            # chiqsa `.env` dagi ALLOWED_HOSTS ga subdomen qo'shilmagan.
            "SERVER_NAME": "paynet.listening.uz",
        }
        if auth:
            raw = f"{settings.PAYNET_LOGIN}:{password or settings.PAYNET_PASSWORD}"
            extra["HTTP_AUTHORIZATION"] = "Basic " + base64.b64encode(raw.encode()).decode()
        response = Client().post("/api/paynet/", data=body,
                                 content_type="application/json", **extra)
        try:
            payload = response.json()
        except ValueError:
            payload = {"_raw": response.content[:400].decode("utf-8", "replace")}
        return response.status_code, payload

    def _emit(self, text, style=None):
        """Konsol ko'tara olmaydigan belgilarni almashtirib yozadi.

        Paynet javoblaridagi `error_note` RUS tilida (hujjat talabi), Windows
        konsoli esa cp1252 da ishlaydi va kirill harflarida butun buyruqni
        `UnicodeEncodeError` bilan yiqitadi. Diagnostika vositasi kodlash
        tufayli ishlamay qolmasligi kerak.
        """
        encoding = getattr(self.stdout._out, "encoding", None) or "utf-8"
        try:
            text.encode(encoding)
        except (UnicodeEncodeError, LookupError):
            text = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
        self.stdout.write(style(text) if style else text)

    def _ok(self, text):
        self._emit(f"  [OK]   {text}", self.style.SUCCESS)

    def _fail(self, text):
        self.failures += 1
        self._emit(f"  [XATO] {text}", self.style.ERROR)

    def _info(self, text):
        self._emit(f"         {text}")

    def _head(self, text):
        self.stdout.write("")
        self._emit(text, self.style.MIGRATE_HEADING)

    # ── Asosiy ───────────────────────────────────────────────────────────
    def handle(self, *args, **options):
        self.failures = 0
        client_id = str(options["client_id"]).strip()
        service_id = int(settings.PAYNET_SERVICE_IDS[0]) if settings.PAYNET_SERVICE_IDS else 1

        self._head("0. Sozlamalar")
        if not settings.PAYNET_LOGIN or not settings.PAYNET_PASSWORD:
            raise CommandError(
                "PAYNET_LOGIN / PAYNET_PASSWORD bo'sh. `.env` ni to'ldiring va "
                "`docker compose up -d web` qiling."
            )
        self._ok(f"login = {settings.PAYNET_LOGIN}")
        self._ok(f"parol = {'*' * len(settings.PAYNET_PASSWORD)} ({len(settings.PAYNET_PASSWORD)} belgi)")
        if settings.PAYNET_SERVICE_IDS:
            self._ok(f"serviceId = {service_id}")
        else:
            self._fail("PAYNET_SERVICE_IDS bo'sh — prod'da HAR QANDAY servis qabul qilinadi. "
                       "`.env` ga yozing.")
        self._ok(f"Field Name = {settings.PAYNET_CLIENT_FIELD}")
        self._info(f"Field Value = {client_id}  (Paynet'ga shuni berasiz)")

        fields = {settings.PAYNET_CLIENT_FIELD: client_id}

        # ── 1. Asosiy tekshiruv: mijoz topiladimi ────────────────────────
        self._head("1. GetInformation — Paynet to'lovdan OLDIN shuni chaqiradi")
        status, payload = self._call("GetInformation",
                                     {"serviceId": service_id, "fields": fields})
        if status != 200:
            self._fail(f"HTTP {status} qaytdi (200 kutilgandi). Javob: {payload}")
            if status == 400:
                self._info("400 = ALLOWED_HOSTS'da `paynet.listening.uz` yo'q.")
            return self._finish()
        if "error" in payload:
            code = payload["error"].get("code")
            self._fail(f"xato {code}: {payload['error'].get('message')}")
            if code == 302:
                self._info(f"302 = bazada `telegram_id={client_id}` foydalanuvchi YO'Q.")
                self._info("Botga /tolov yozib o'z chat ID'ingizni oling va shuni ishlating.")
            elif code == 305:
                self._info("305 = serviceId mos emas (PAYNET_SERVICE_IDS ni tekshiring).")
            return self._finish()

        result = payload["result"]
        self._ok(f"mijoz topildi: {result['fields'].get('name')}")
        self._info(f"balans: {result['fields'].get('balance')} tiyin "
                   f"({result['fields'].get('balance', 0) // 100} so'm)")
        self._info(f"vaqt: {result.get('timestamp')}  (GMT+5 bo'lishi kerak)")

        # ── 2. Himoya ishlayaptimi ───────────────────────────────────────
        self._head("2. Himoya — noto'g'ri so'rovlar RAD etiladimi")

        status, _ = self._call("GetInformation", {"serviceId": service_id, "fields": fields},
                               password="butunlay-boshqa-parol")
        if status == 401:
            self._ok("noto'g'ri parol -> HTTP 401")
        else:
            self._fail(f"noto'g'ri parol -> HTTP {status} (401 kutilgandi!)")

        status, payload = self._call("GetInformation", {"serviceId": service_id, "fields": fields},
                                     ip=FOREIGN_IP)
        if payload.get("error", {}).get("code") == 601:
            self._ok("begona IP -> 601 (kirish taqiqlandi)")
        else:
            self._fail(f"begona IP rad etilmadi! Javob: {payload}")

        status, payload = self._call("GetInformation",
                                     {"serviceId": service_id,
                                      "fields": {settings.PAYNET_CLIENT_FIELD: "99999999999"}})
        if payload.get("error", {}).get("code") == 302:
            self._ok("noma'lum mijoz -> 302 (mijoz topilmadi)")
        else:
            self._fail(f"noma'lum mijoz uchun 302 kutilgandi. Javob: {payload}")

        if not options["full"]:
            self._head("To'lov yo'li sinalmadi")
            self._info("Pul harakatini ham sinash uchun: --full qo'shing.")
            self._info("(kichik summa yoziladi va DARROV bekor qilinadi)")
            return self._finish()

        # ── 3. To'lov yo'li: yozish -> tekshirish -> bekor qilish ──────────
        self._head("3. To'lov yo'li (yoziladi va darrov bekor qilinadi)")
        from apps.accounts.models import User

        user = User.objects.filter(telegram_id=int(client_id)).first()
        before = Wallet.objects.filter(user=user).values_list("balance_tiyin", flat=True).first() or 0
        amount = int(settings.PAYNET_MIN_AMOUNT_TIYIN)
        # Haqiqiy Paynet ID'lari bilan to'qnashmasin — 9xxx bilan boshlaymiz.
        trn_id = 9_000_000_000_000 + int(time.time())
        self._info(f"boshlang'ich balans: {before} tiyin")

        status, payload = self._call("PerformTransaction", {
            "amount": amount, "serviceId": service_id,
            "transactionId": trn_id, "fields": fields,
        })
        if "error" in payload:
            self._fail(f"PerformTransaction xato: {payload['error']}")
            return self._finish()
        provider_trn_id = payload["result"]["providerTrnId"]
        self._ok(f"to'lov qabul qilindi, providerTrnId = {provider_trn_id}")
        self._info(f"javobdagi balans: {payload['result']['fields'].get('balance')} tiyin")

        # Takroriy so'rov — Paynet javobni olmay qolsa aynan shunday qiladi.
        status, repeat = self._call("PerformTransaction", {
            "amount": amount, "serviceId": service_id,
            "transactionId": trn_id, "fields": fields,
        })
        if repeat.get("result", {}).get("providerTrnId") == provider_trn_id:
            self._ok("takroriy so'rov -> O'SHA providerTrnId (pul ikki marta yozilmadi)")
        else:
            self._fail(f"takroriy so'rov boshqacha javob berdi: {repeat}")

        status, payload = self._call("CheckTransaction", {
            "serviceId": service_id, "transactionId": trn_id,
        })
        state = payload.get("result", {}).get("transactionState")
        if state == 1:
            self._ok("CheckTransaction -> 1 (muvaffaqiyatli)")
        else:
            self._fail(f"CheckTransaction -> {state} (1 kutilgandi)")

        status, payload = self._call("CancelTransaction", {
            "serviceId": service_id, "transactionId": trn_id,
        })
        state = payload.get("result", {}).get("transactionState")
        if state == 2:
            self._ok("CancelTransaction -> 2 (bekor qilindi)")
        else:
            self._fail(f"CancelTransaction -> {state} (2 kutilgandi). Javob: {payload}")

        after = Wallet.objects.filter(user=user).values_list("balance_tiyin", flat=True).first() or 0
        if after == before:
            self._ok(f"balans tiklandi: {after} tiyin (o'zgarmadi)")
        else:
            self._fail(f"BALANS O'ZGARIB QOLDI: {before} -> {after} tiyin. "
                       f"Admin'dan tuzating: /admin/billing/wallet/")

        txn = PaynetTransaction.objects.filter(transaction_id=trn_id).first()
        self._info(f"test yozuvi admin'da qoladi: Paynet tranzaksiyasi #{txn.pk} "
                   f"(holati: bekor qilingan)" if txn else "")

        return self._finish()

    def _finish(self):
        self.stdout.write("")
        if self.failures:
            self._emit(f"  {self.failures} ta muammo topildi — "
                       "Paynet'ga BERISHDAN OLDIN tuzating.", self.style.ERROR)
        else:
            self._emit("  Hammasi joyida. Paynet'ga ma'lumot berishingiz mumkin.",
                       self.style.SUCCESS)
        self.stdout.write("")

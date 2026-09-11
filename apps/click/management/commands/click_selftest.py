"""Click integratsiyasini O'ZIMIZ sinash — Click'ning testidan OLDIN.

    python manage.py click_selftest --client-id 123456789
    python manage.py click_selftest --client-id 123456789 --full

Click'ning o'z testlash dasturi bor (docs.click.uz/click-api-testing), lekin
u bizga faqat "o'tdi / o'tmadi" deydi. Bu buyruq esa AYNAN qaysi bosqichda
nima noto'g'ri ekanini ko'rsatadi: imzo, `service_id`, `ALLOWED_HOSTS`,
buyurtma topilishi, takroriy `Complete`.

## Imzo mustaqil hisoblanadi

Buyruq `signature.py` ni CHAQIRMAYDI — MD5 ni o'zi qayta hisoblaydi. Agar
formulada xato bo'lsa, ikkala tomon bir xil noto'g'ri hisoblab test yashil
qolib ketardi.

## Xavfsizlik

`--full` siz: faqat `Prepare` va xato holatlari — pul harakati YO'Q.

`--full` bilan: eng kichik summa (`PAYNET_MIN_AMOUNT_TIYIN`) bilan
`Complete` sinaladi va **oxirida qaytarib olinadi** (balans tiklanadi,
sinov buyurtmasi o'chiriladi). Tarif yoqilishi sinalmaydi — summa ataylab
tarif narxidan kichik; u yo'l 26 ta unit-test bilan qoplangan.
"""
import hashlib
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.test import Client

from apps.billing.models import Plan, Wallet
from apps.click.links import payment_url
from apps.click.models import ClickOrder


class Command(BaseCommand):
    help = "Click SHOP-API (Prepare/Complete) ni ichkaridan sinaydi."

    def add_arguments(self, parser):
        parser.add_argument("--client-id", required=True,
                            help="Telegram chat ID — sinov buyurtmasi shu foydalanuvchiga.")
        parser.add_argument("--full", action="store_true",
                            help="`Complete` ni ham sinaydi (pul yoziladi va QAYTARILADI).")
        parser.add_argument("--keep", action="store_true",
                            help="Sinov buyurtmasini O'CHIRMAYDI — Click'ning o'z "
                                 "testlash dasturiga `merchant_trans_id` kerak bo'lganda.")

    # ── Imzo — mustaqil hisob ────────────────────────────────────────────
    def _sign(self, data, action):
        raw = (f"{data['click_trans_id']}{data['service_id']}"
               f"{settings.CLICK_SECRET_KEY}{data['merchant_trans_id']}"
               f"{data.get('merchant_prepare_id', '') if str(action) == '1' else ''}"
               f"{data['amount']}{data['action']}{data['sign_time']}")
        return hashlib.md5(raw.encode()).hexdigest()

    def _payload(self, order, action, *, amount=None, click_error=0, prepare_id=None):
        data = {
            "click_trans_id": self.click_trans_id,
            "service_id": settings.CLICK_SERVICE_ID,
            "click_paydoc_id": 555001,
            "merchant_trans_id": str(order.pk),
            "amount": amount if amount is not None else self.amount_str,
            "action": action,
            "error": click_error,
            "error_note": "Success" if click_error == 0 else "Cancelled by user",
            "sign_time": "2026-09-11 12:00:00",
        }
        if action == 1:
            data["merchant_prepare_id"] = str(order.pk if prepare_id is None else prepare_id)
        data["sign_string"] = self._sign(data, action)
        return data

    def _post(self, path, data):
        # Click `application/x-www-form-urlencoded` yuboradi — aynan shunday.
        response = Client().post(path, data, SERVER_NAME="click.listening.uz")
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, {"_raw": response.content[:300].decode("utf-8", "replace")}

    # ── Chiqish ──────────────────────────────────────────────────────────
    def _emit(self, text, style=None):
        encoding = getattr(self.stdout._out, "encoding", None) or "utf-8"
        try:
            text.encode(encoding)
        except (UnicodeEncodeError, LookupError):
            text = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
        self.stdout.write(style(text) if style else text)

    def _ok(self, t):
        self._emit(f"  [OK]   {t}", self.style.SUCCESS)

    def _fail(self, t):
        self.failures += 1
        self._emit(f"  [XATO] {t}", self.style.ERROR)

    def _info(self, t):
        self._emit(f"         {t}")

    def _head(self, t):
        self.stdout.write("")
        self._emit(t, self.style.MIGRATE_HEADING)

    def _expect(self, label, payload, code):
        got = payload.get("error")
        if got == code:
            self._ok(f"{label} -> {code}")
        else:
            self._fail(f"{label} -> {got} ({code} kutilgandi). Javob: {payload}")

    # ── Asosiy ───────────────────────────────────────────────────────────
    def handle(self, *args, **options):
        from apps.accounts.models import User

        self.failures = 0
        client_id = str(options["client_id"]).strip()

        self._head("0. Sozlamalar")
        # `CLICK_MERCHANT_ID` bu ro'yxatda ATAYLAB yo'q — u ixtiyoriy
        # (kabinetda bunday maydon yo'q; batafsil `apps/click/links.py`).
        missing = [n for n in ("CLICK_SERVICE_ID", "CLICK_SECRET_KEY")
                   if not getattr(settings, n)]
        if missing:
            raise CommandError(
                f"{', '.join(missing)} bo'sh. Qiymatlarni merchant.click.uz -> "
                "\"Сервисы\" dan oling, `.env` ga yozing va "
                "`docker compose up -d --force-recreate web` qiling."
            )
        self._ok(f"CLICK_SERVICE_ID  = {settings.CLICK_SERVICE_ID}")
        self._ok(f"CLICK_SECRET_KEY  = {'*' * len(settings.CLICK_SECRET_KEY)} "
                 f"({len(settings.CLICK_SECRET_KEY)} belgi)")
        if settings.CLICK_MERCHANT_ID:
            self._ok(f"CLICK_MERCHANT_ID = {settings.CLICK_MERCHANT_ID}")
        else:
            self._info("CLICK_MERCHANT_ID = bo'sh (ixtiyoriy — havolaga qo'shilmaydi)")

        user = User.objects.filter(telegram_id=int(client_id)).first()
        if user is None:
            raise CommandError(
                f"telegram_id={client_id} foydalanuvchi bazada YO'Q. "
                "Botga /tolov yozib o'z chat ID'ingizni oling."
            )
        plan = Plan.objects.filter(is_active=True, is_default=False,
                                   price_uzs__gt=0).order_by("price_uzs").first()
        if plan is None:
            raise CommandError("Pullik tarif topilmadi (Plan jadvali bo'sh?).")

        # Summa ATAYLAB tarif narxidan kichik — `Complete` da tarif yoqilmasin,
        # pul balansda qolsin va uni toza qaytarib olaylik.
        amount_tiyin = int(settings.PAYNET_MIN_AMOUNT_TIYIN)
        amount_uzs = amount_tiyin // 100
        self.amount_str = f"{amount_uzs}.00"        # Click aynan shunday yuboradi
        self.click_trans_id = 900_000_000 + int(time.time()) % 1_000_000

        order = ClickOrder.objects.create(user=user, plan=plan, months=1,
                                          amount_uzs=amount_uzs)
        self._info(f"sinov buyurtmasi #{order.pk}: {user} / {plan} / {amount_uzs} so'm")
        # Havolani HOZIR yasab olamiz: pastda buyurtma o'chiriladi va
        # `order.pk` `None` bo'lib qoladi (Django `delete()` shunday qiladi).
        pay_url = payment_url(order)

        keep = options["keep"]
        try:
            self._run(order)
        finally:
            if not options["full"] and not keep:
                order.refresh_from_db()
                if order.status != ClickOrder.Status.CONFIRMED:
                    order.delete()

        if options["full"]:
            self._complete_flow(order, user, keep=keep)

        self._head("To'lov havolasi (saytdagi tugma shuni ochadi)")
        self._info(pay_url)

        if keep and not options["full"]:
            self._info(f"Click testeri uchun buyurtma QOLDIRILDI: "
                       f"merchant_trans_id = {order.pk}, summa = {amount_uzs} so'm")

        if not options["full"]:
            self._head("`Complete` sinalmadi")
            self._info("Pul harakatini ham sinash uchun: --full qo'shing.")
        return self._finish()

    def _run(self, order):
        self._head("1. Prepare — Click to'lovdan OLDIN shuni yuboradi")
        status, payload = self._post("/api/click/prepare/", self._payload(order, 0))
        if status != 200:
            self._fail(f"HTTP {status} (200 kutilgandi). Javob: {payload}")
            if status == 400:
                self._info("400 = ALLOWED_HOSTS'da `click.listening.uz` yo'q.")
            return
        self._expect("to'g'ri imzo", payload, 0)
        if payload.get("merchant_prepare_id") == order.pk:
            self._ok(f"merchant_prepare_id = {order.pk} (buyurtma raqamimiz)")
        else:
            self._fail(f"merchant_prepare_id noto'g'ri: {payload.get('merchant_prepare_id')}")

        self._head("2. Himoya — soxta so'rovlar RAD etiladimi")

        bad = self._payload(order, 0)
        bad["sign_string"] = "00000000000000000000000000000000"
        _, payload = self._post("/api/click/prepare/", bad)
        self._expect("soxta imzo", payload, -1)

        wrong = self._payload(order, 0, amount="999999.00")
        _, payload = self._post("/api/click/prepare/", wrong)
        self._expect("noto'g'ri summa", payload, -2)

        ghost = ClickOrder(pk=999_999_999, amount_uzs=order.amount_uzs)
        _, payload = self._post("/api/click/prepare/", self._payload(ghost, 0))
        self._expect("mavjud bo'lmagan buyurtma", payload, -5)

        incomplete = self._payload(order, 0)
        del incomplete["click_paydoc_id"]
        _, payload = self._post("/api/click/prepare/", incomplete)
        self._expect("maydon yetishmasa", payload, -8)

    def _complete_flow(self, order, user, keep=False):
        self._head("3. Complete — pul keldi (oxirida QAYTARILADI)")
        before = Wallet.objects.filter(user=user).values_list(
            "balance_tiyin", flat=True).first() or 0
        self._info(f"boshlang'ich balans: {before} tiyin")

        _, payload = self._post("/api/click/complete/", self._payload(order, 1))
        self._expect("to'lov tasdiqlandi", payload, 0)

        order.refresh_from_db()
        after = Wallet.objects.filter(user=user).values_list(
            "balance_tiyin", flat=True).first() or 0
        if after == before + order.amount_tiyin:
            self._ok(f"balans oshdi: {before} -> {after} tiyin")
        else:
            self._fail(f"balans kutilganidek oshmadi: {before} -> {after}")

        _, payload = self._post("/api/click/complete/", self._payload(order, 1))
        self._expect("takroriy Complete (pul ikki marta yozilmasin)", payload, -4)

        final = Wallet.objects.filter(user=user).values_list(
            "balance_tiyin", flat=True).first() or 0
        if final == after:
            self._ok("takroriy so'rov balansga TEGMADI")
        else:
            self._fail(f"takroriy so'rov balansni o'zgartirdi: {after} -> {final}")

        # Click O'ZI xato yuborsa — pul kelmagan, hamyonga tegilmasligi kerak.
        other = ClickOrder.objects.create(user=user, plan=order.plan, months=1,
                                          amount_uzs=order.amount_uzs)
        self._post("/api/click/prepare/", self._payload(other, 0))
        _, payload = self._post("/api/click/complete/",
                                self._payload(other, 1, click_error=-5017))
        self._expect("Click xato yubordi (bekor qilingan)", payload, -9)
        cancelled_balance = Wallet.objects.filter(user=user).values_list(
            "balance_tiyin", flat=True).first() or 0
        if cancelled_balance == final:
            self._ok("bekor qilingan to'lovda pul YOZILMADI")
        else:
            self._fail(f"bekor qilingan to'lovda balans o'zgardi: {final} -> {cancelled_balance}")
        other.delete()

        # ── Qaytarib olish ──
        self._head("4. Tozalash — sinov puli qaytarib olinadi")
        wallet = Wallet.objects.get(user=user)
        wallet.balance_tiyin -= order.amount_tiyin
        wallet.save(update_fields=["balance_tiyin", "updated_at"])
        if keep:
            # `--keep`: buyurtma qoladi, lekin u allaqachon `confirmed` —
            # Click testeri unga `-4 Already paid` oladi. Shu bois YANGI,
            # tegilmagan buyurtma yaratib beramiz.
            fresh = ClickOrder.objects.create(user=user, plan=order.plan, months=1,
                                              amount_uzs=order.amount_uzs)
            self._info(f"Click testeri uchun tayyor buyurtma: "
                       f"merchant_trans_id = {fresh.pk}, summa = {fresh.amount_uzs} so'm")
        order.delete()
        restored = Wallet.objects.get(user=user).balance_tiyin
        if restored == before:
            self._ok(f"balans tiklandi: {restored} tiyin (o'zgarmadi)")
        else:
            self._fail(f"BALANS O'ZGARIB QOLDI: {before} -> {restored}. "
                       "Admin'dan tuzating: /admin/billing/wallet/")

    def _finish(self):
        self.stdout.write("")
        if self.failures:
            self._emit(f"  {self.failures} ta muammo topildi — Click testidan OLDIN tuzating.",
                       self.style.ERROR)
        else:
            self._emit("  Hammasi joyida. Click testlash dasturini ishlatishingiz mumkin.",
                       self.style.SUCCESS)
        self.stdout.write("")

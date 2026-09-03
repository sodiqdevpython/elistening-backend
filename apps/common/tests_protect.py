"""Kontent o'rami (`apps/common/protect.py`) va uning API'dagi ta'siri.

**Eng muhimi — qat'iy VEKTOR testi.** Algoritm uch joyda takrorlangan
(python + sayt + mobil). Bittasi o'zgarsa qolgan ikkitasi kontentni ocha
olmaydi va foydalanuvchi bo'sh savollarni ko'radi. Quyidagi vektor ana shu
"jimgina buzilish" ni ushlaydi: agar u yiqilsa, `frontend/src/utils/protect.ts`
va `mobile/src/utils/protect.ts` ni ham AYNAN shunday o'zgartirish shart.
"""
import base64
import json

from django.test import TestCase, override_settings

from apps.catalog.models import Dictation, Short
from apps.common import protect as P


class VectorTests(TestCase):
    @override_settings(CONTENT_SECRET="sodiq2005.py")
    def test_keystream_is_stable(self):
        """Kalit oqimining boshi — algoritm o'zgarmaganining barmoq izi."""
        P._keystream.cache_clear()
        head = list(P._keystream("sodiq2005.py")[:8])
        self.assertEqual(head, [118, 184, 117, 247, 38, 46, 247, 134])

    @override_settings(CONTENT_SECRET="sodiq2005.py")
    def test_known_blob_decodes(self):
        """Qo'lda yasalgan blob ochilishi kerak (mijoz kodi ham shuni qiladi)."""
        payload = {"text": "Hello there.", "n": 42}
        raw = json.dumps(payload, separators=(",", ":")).encode("ascii")
        offset = 1234
        blob = P.PREFIX + base64.b64encode(
            offset.to_bytes(2, "big") + P._xor(raw, "sodiq2005.py", offset)
        ).decode("ascii")
        self.assertEqual(P.unprotect(blob), payload)

    @override_settings(CONTENT_SECRET="sodiq2005.py")
    def test_roundtrip_keeps_everything(self):
        cases = [
            {"body": [{"start_ms": 0, "end_ms": 3800, "text": "Hello there."}]},
            {"uz": "O‘zbekcha — tire, apostrof: don't, \"qo'shtirnoq\", emoji 🎁"},
            {"empty": {}, "null": None, "nums": [1, 2.5, -3]},
            {"big": [{"i": i, "w": f"word{i}"} for i in range(2000)]},
        ]
        for case in cases:
            with self.subTest(case=str(case)[:40]):
                self.assertEqual(P.unprotect(P.protect(case)), case)

    @override_settings(CONTENT_SECRET="sodiq2005.py")
    def test_output_differs_every_time(self):
        """Tasodifiy `offset` — bir xil kontent har safar boshqacha ko'rinadi."""
        payload = {"text": "same"}
        blobs = {P.protect(payload) for _ in range(20)}
        self.assertGreater(len(blobs), 1)

    @override_settings(CONTENT_SECRET="sodiq2005.py")
    def test_plain_text_is_not_visible(self):
        blob = P.protect({"answer": "Monday", "proof_from_text": "[12.3] on Monday"})
        self.assertNotIn("Monday", blob)
        self.assertNotIn("proof_from_text", blob)


class ApiShapeTests(TestCase):
    """API javobida transkript/savollar ochiq holda TURMASLIGI kerak."""

    def setUp(self):
        # Feed FAQAT to'liq tayyor shortslarni beradi (`ShortViewSet`):
        # chop etilgan + transkript DONE + o'lik emas + MCQ bor.
        self.short = Short.objects.create(
            youtube_id="abc12345678", title="Sinov", duration_sec=30,
            is_published=True, is_dead=False,
            transcription_status=Short.TranscriptionStatus.DONE,
            mcq_questions=[{"question": "What?", "answer": "B",
                            "proof_from_text": "[3.4] secret words"}],
        )
        self.dictation = Dictation.objects.create(
            title="Sinov diktant", type="news", is_published=True,
            is_media=True, youtube_link="https://youtu.be/abc12345678",
            body=[{"start_ms": 0, "end_ms": 1000, "text": "secret sentence"}],
            words_json=[{"start": 0.1, "end": 0.4, "word": "secret"}],
        )

    def test_short_list_is_wrapped(self):
        res = self.client.get("/api/shorts/")
        row = res.data["results"][0]
        self.assertIn("enc", row)
        self.assertNotIn("mcq_questions", row)
        self.assertNotIn("secret words", str(res.data))
        # Ochilgach hamma narsa joyida
        opened = P.unprotect(row["enc"])
        self.assertEqual(opened["mcq_questions"][0]["answer"], "B")

    def test_dictation_detail_is_wrapped(self):
        res = self.client.get(f"/api/dictations/{self.dictation.slug}/")
        self.assertIn("enc", res.data)
        self.assertNotIn("body", res.data)
        self.assertNotIn("secret sentence", str(res.data))
        opened = P.unprotect(res.data["enc"])
        self.assertEqual(opened["body"][0]["text"], "secret sentence")

    def test_open_fields_stay_open(self):
        """Sarlavha, daraja, davomiylik o'ralmaydi — ular ro'yxatda kerak."""
        res = self.client.get("/api/shorts/")
        row = res.data["results"][0]
        self.assertEqual(row["title"], "Sinov")
        self.assertEqual(row["duration_sec"], 30)

    @override_settings(PROTECT_CONTENT=False)
    def test_can_be_switched_off(self):
        """O'chirilganda eski (ochiq) javob qaytadi — debug/admin uchun."""
        res = self.client.get("/api/shorts/")
        row = res.data["results"][0]
        self.assertNotIn("enc", row)
        self.assertIn("mcq_questions", row)

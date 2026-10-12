"""Shaxsiy lug'at (`vocab.py`) va xato yozilgan so'zlar (`DictationProgress.mistakes`)."""
import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.catalog.models import Dictation, DictationProgress, VocabWord

User = get_user_model()
BODY = [{"start_ms": 0, "end_ms": 1000, "text": "The Peacocks ruled Gongmen City."}]


class VocabApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="learner", password="pw12345!")
        self.client.force_login(self.user)
        self.lesson = Dictation.objects.create(title="Kung Fu", type=Dictation.Type.SHORT_STORY,
                                               body=BODY, is_published=True)

    def add(self, word, **extra):
        return self.client.post("/api/me/vocab/", json.dumps({"word": word, **extra}),
                                content_type="application/json")

    def test_requires_login(self):
        self.client.logout()
        self.assertIn(self.client.get("/api/me/vocab/").status_code, (401, 403))
        self.assertIn(self.add("Peacocks").status_code, (401, 403))

    def test_add_cleans_word_and_keeps_context(self):
        r = self.add("(Peacocks),", context="  The Peacocks   ruled. ", dictation=self.lesson.slug)
        self.assertEqual(r.status_code, 201)
        body = r.json()
        self.assertEqual((body["word"], body["display"], body["context"]),
                         ("peacocks", "Peacocks", "The Peacocks ruled."))
        self.assertEqual(body["dictation"]["slug"], self.lesson.slug)
        self.assertEqual(body["dictation"]["type_slug"], "short-stories")

    def test_same_word_is_stored_once(self):
        first = self.add("City.").json()
        again = self.add("city")
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json()["id"], first["id"])
        self.assertEqual(VocabWord.objects.filter(user=self.user).count(), 1)

    def test_numbers_and_punctuation_are_not_words(self):
        for raw in ("(797)", "285-6959", "—", ""):
            self.assertEqual(self.add(raw).status_code, 400, raw)
        self.assertEqual(self.add("don't").json()["word"], "don't")

    def test_list_words_map_and_delete_own_only(self):
        a = self.add("Peacocks").json()
        b = self.add("ruled").json()
        listed = self.client.get("/api/me/vocab/").json()
        self.assertEqual(listed["count"], 2)
        self.assertEqual([r["word"] for r in listed["results"]], ["ruled", "peacocks"])  # yangisi birinchi
        self.assertEqual(self.client.get("/api/me/vocab/words/").json()["words"],
                         {"peacocks": a["id"], "ruled": b["id"]})

        other = User.objects.create_user(username="other", password="pw12345!")
        self.client.force_login(other)
        self.assertEqual(self.client.delete(f"/api/me/vocab/{a['id']}/").status_code, 404)
        self.assertEqual(self.client.get("/api/me/vocab/").json()["count"], 0)  # begonaniki ko'rinmaydi

        self.client.force_login(self.user)
        self.assertEqual(self.client.delete(f"/api/me/vocab/{a['id']}/").status_code, 204)
        self.assertEqual(list(VocabWord.objects.values_list("word", flat=True)), ["ruled"])

    def test_deleted_lesson_keeps_word(self):
        self.add("Peacocks", dictation=self.lesson.slug)
        self.lesson.delete()
        row = self.client.get("/api/me/vocab/").json()["results"][0]
        self.assertEqual((row["word"], row["dictation"]), ("peacocks", None))


class MistakesProgressTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="writer", password="pw12345!")
        self.client.force_login(self.user)
        self.lesson = Dictation.objects.create(title="Lesson", type=Dictation.Type.SHORT_STORY,
                                               body=BODY, is_published=True)
        self.url = f"/api/dictations/{self.lesson.slug}"

    def post(self, path, payload):
        return self.client.post(f"{self.url}/{path}/", json.dumps(payload), content_type="application/json")

    def test_mistakes_saved_and_returned_for_resume(self):
        r = self.post("progress", {"percent": 50, "first_try": {"0": "wrong"}, "mistakes": {"0": [1, 3]}})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get(f"{self.url}/progress/").json()["mistakes"], {"0": [1, 3]})

    def test_bad_mistakes_rejected(self):
        for bad in ({"x": [1]}, {"0": "1"}, {"0": [-1]}, {"0": [True]}, [1, 2]):
            self.assertEqual(self.post("progress", {"mistakes": bad}).status_code, 400, bad)

    def test_complete_clears_mistakes(self):
        self.post("progress", {"mistakes": {"0": [1]}})
        self.post("complete", {"accuracy": 50, "active_seconds": 30})
        self.assertEqual(DictationProgress.objects.get(user=self.user).mistakes, {})

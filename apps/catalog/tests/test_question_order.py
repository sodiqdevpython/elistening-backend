"""Savollar ketma-ketligi testlari.

Claude promptda "isbot vaqti bo'yicha o'sish tartibida ber" deb so'ralgan,
lekin LLM buni har doim ham bajarmaydi. Shu bois server AI natijasini
majburan saralaydi (`shorts_pipeline._order_quiz`) — IELTS'dagidek 1-savolning
javobi eng oldin, 2-niki keyinroq eshitiladi.
"""
from django.test import SimpleTestCase

from apps.catalog.shorts_pipeline import _order_quiz, _proof_seconds, _sort_by_proof


def q(name, proof):
    return {"question": name, "proof_from_text": proof}


class ProofSecondsTests(SimpleTestCase):
    def test_parses_leading_timestamp(self):
        self.assertEqual(_proof_seconds(q("a", "[12.8] hello there")), 12.8)

    def test_integer_timestamp(self):
        self.assertEqual(_proof_seconds(q("a", "[7] hi")), 7.0)

    def test_missing_timestamp_returns_none(self):
        self.assertIsNone(_proof_seconds(q("a", "")))
        self.assertIsNone(_proof_seconds(q("a", "no timestamp here")))


class SortByProofTests(SimpleTestCase):
    def test_sorts_ascending(self):
        out = _sort_by_proof([
            q("c", "[45.2] c"), q("a", "[12.8] a"), q("b", "[30.0] b"),
        ])
        self.assertEqual([x["question"] for x in out], ["a", "b", "c"])

    def test_not_given_keeps_its_neighbour(self):
        """Isbotsiz savol ("Not given") o'zi ergashgan savol yonida qoladi."""
        out = _sort_by_proof([
            q("c", "[45.2] c"), q("a", "[12.8] a"), q("ng", ""), q("b", "[30.0] b"),
        ])
        self.assertEqual([x["question"] for x in out], ["a", "ng", "b", "c"])

    def test_already_ordered_is_untouched(self):
        items = [q("a", "[1.0] a"), q("b", "[2.0] b"), q("c", "[3.0] c")]
        self.assertEqual(
            [x["question"] for x in _sort_by_proof(items)], ["a", "b", "c"],
        )

    def test_equal_timestamps_keep_ai_order(self):
        """Barqaror saralash — bir xil vaqtda AI bergan tartib buzilmaydi."""
        out = _sort_by_proof([q("first", "[5.0] x"), q("second", "[5.0] y")])
        self.assertEqual([x["question"] for x in out], ["first", "second"])

    def test_timestamps_are_monotonic_after_sort(self):
        out = _sort_by_proof([
            q("a", "[9.9] a"), q("b", "[0.4] b"), q("c", ""), q("d", "[4.2] d"),
        ])
        secs = [_proof_seconds(x) for x in out if _proof_seconds(x) is not None]
        self.assertEqual(secs, sorted(secs))


class OrderQuizTests(SimpleTestCase):
    def test_each_list_sorted_independently(self):
        """Har ro'yxat alohida boshdan-oxirgacha yuradi (MCQ, keyin TFNG, ...)."""
        quiz = {
            "multiple_choice_questions": [q("m2", "[40.0] x"), q("m1", "[5.0] y")],
            "tfng_questions": [q("t2", "[50.0] x"), q("t1", "[3.0] y")],
            "fill_gap_questions": [q("f2", "[20.0] x"), q("f1", "[1.0] y")],
        }
        out = _order_quiz(quiz)
        self.assertEqual(
            [x["question"] for x in out["multiple_choice_questions"]], ["m1", "m2"],
        )
        self.assertEqual([x["question"] for x in out["tfng_questions"]], ["t1", "t2"])
        self.assertEqual([x["question"] for x in out["fill_gap_questions"]], ["f1", "f2"])

    def test_legacy_true_false_key_also_sorted(self):
        quiz = {"true_false_questions": [q("b", "[8.0] x"), q("a", "[2.0] y")]}
        out = _order_quiz(quiz)
        self.assertEqual([x["question"] for x in out["true_false_questions"]], ["a", "b"])

    def test_missing_lists_are_ignored(self):
        self.assertEqual(_order_quiz({}), {})

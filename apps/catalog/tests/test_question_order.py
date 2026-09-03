"""Savollar ketma-ketligi testlari.

Claude promptda ketma-ketlik qat'iy talab qilingan (`ai/prompt_shorts.txt`,
5-qoida), lekin LLM buni har doim bajarmaydi. Shu bois server ikki bosqichda
majburlaydi (`shorts_pipeline._order_quiz`):

1. har ro'yxat isbot vaqti bo'yicha saralanadi;
2. butun ro'yxat bo'ylab xronologik `number` qo'yiladi — IELTS'dagidek
   1-savolning javobi eng oldin, 2-niki keyinroq eshitiladi.

Ikkinchi bosqich foydalanuvchi shikoyatidan keyin qo'shildi: *"oldin 3, 1,
2, 4 shu tartibda javob kelayabdi"* — MCQ videoni boshdan-oxir bosib
o'tardi, keyin TFNG YANA boshidan boshlanardi.
"""
from django.test import SimpleTestCase

from apps.catalog.shorts_pipeline import (
    _order_quiz,
    _proof_seconds,
    _sort_by_proof,
    sequence_is_chronological,
)


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


class GlobalNumberTests(SimpleTestCase):
    """`number` — VIDEO bo'yicha xronologik o'rin (bo'limlardan qat'i nazar)."""

    def _numbers(self, out):
        """Ko'rsatish tartibidagi (MCQ → TFNG → Fill) raqamlar."""
        return [
            x["number"]
            for key in ("multiple_choice_questions", "tfng_questions", "fill_gap_questions")
            for x in out.get(key, [])
        ]

    def test_sections_in_order_get_natural_numbers(self):
        """AI 5-qoidani bajargan holat — raqamlar 1..N ketma-ket."""
        out = _order_quiz({
            "multiple_choice_questions": [q("m1", "[4.0] a"), q("m2", "[18.5] b")],
            "tfng_questions": [q("t1", "[33.2] c"), q("t2", "[51.0] d")],
        })
        self.assertEqual(self._numbers(out), [1, 2, 3, 4])

    def test_overlapping_sections_are_renumbered_chronologically(self):
        """AI 5-qoidani BUZGAN holat — foydalanuvchi ko'rgan "3, 1, 2, 4".

        MCQ [4.0, 51.0], TFNG [18.5, 33.2] — pozitsiya bo'yicha raqamlansa
        2-savol 51-soniyada, 3-savol esa 18.5-soniyada bo'lardi. `number`
        buni to'g'rilaydi: vaqt bo'yicha 4.0 → 18.5 → 33.2 → 51.0.
        """
        out = _order_quiz({
            "multiple_choice_questions": [q("m1", "[4.0] a"), q("m2", "[51.0] b")],
            "tfng_questions": [q("t1", "[18.5] c"), q("t2", "[33.2] d")],
        })
        self.assertEqual(self._numbers(out), [1, 4, 2, 3])

    def test_numbers_increase_with_time(self):
        """Asosiy invariant: vaqt bo'yicha saralasak raqamlar ham o'sadi."""
        out = _order_quiz({
            "multiple_choice_questions": [q("m1", "[70.0] a"), q("m2", "[10.0] b")],
            "tfng_questions": [q("t1", "[40.0] c")],
            "fill_gap_questions": [q("f1", "[25.0] d")],
        })
        pairs = sorted(
            (
                (_proof_seconds(x), x["number"])
                for key in ("multiple_choice_questions", "tfng_questions", "fill_gap_questions")
                for x in out.get(key, [])
            ),
        )
        self.assertEqual([n for _, n in pairs], [1, 2, 3, 4])

    def test_not_given_inherits_neighbour_position(self):
        """Isbotsiz savol qo'shnisidan keyin turadi, raqami ham shunga qarab."""
        out = _order_quiz({
            "multiple_choice_questions": [q("m1", "[5.0] a")],
            "tfng_questions": [q("t1", "[20.0] b"), q("ng", "")],
        })
        self.assertEqual(self._numbers(out), [1, 2, 3])

    def test_every_question_gets_a_unique_number(self):
        out = _order_quiz({
            "multiple_choice_questions": [q("a", "[5.0] x"), q("b", "[5.0] y")],
            "tfng_questions": [q("c", "[5.0] z")],
        })
        self.assertEqual(sorted(self._numbers(out)), [1, 2, 3])


class SequenceCheckTests(SimpleTestCase):
    """`sequence_is_chronological` — AI 5-qoidani bajardimi (faqat log uchun)."""

    def test_true_when_sections_are_consecutive_windows(self):
        self.assertTrue(sequence_is_chronological({
            "multiple_choice_questions": [q("m1", "[4.0] a"), q("m2", "[18.5] b")],
            "tfng_questions": [q("t1", "[33.2] c")],
        }))

    def test_false_when_sections_overlap(self):
        self.assertFalse(sequence_is_chronological({
            "multiple_choice_questions": [q("m1", "[4.0] a"), q("m2", "[51.0] b")],
            "tfng_questions": [q("t1", "[18.5] c")],
        }))

    def test_empty_quiz_is_fine(self):
        self.assertTrue(sequence_is_chronological({}))

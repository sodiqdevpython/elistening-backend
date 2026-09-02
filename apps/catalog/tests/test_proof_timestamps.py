"""`align_proof_timestamps` — AI bergan `[t]` larni haqiqiy vaqtga moslash."""
from django.test import SimpleTestCase

from apps.catalog.shorts_pipeline import align_proof_timestamps


def words(pairs):
    """[(so'z, start)] → Whisper `words_json` shakli."""
    return [{"word": w, "start": s, "end": s + 0.3} for w, s in pairs]


# "our goal is chaos" 33.2 da, "we'll build the myth" esa 47.0 da aytiladi.
WORDS = words([
    ("Our", 33.2), ("goal", 33.5), ("is", 33.8), ("chaos", 34.0),
    ("and", 40.0), ("nothing", 40.3), ("else", 40.6), ("matters", 40.9),
    ("We'll", 47.0), ("build", 47.3), ("the", 47.6), ("myth", 47.9),
])


class AlignProofTimestampsTests(SimpleTestCase):
    def test_wrong_timestamp_is_corrected(self):
        """AI ikkinchi bo'lakka ham birinchi qatorning vaqtini qo'ygan."""
        quiz = {"multiple_choice_questions": [{
            "proof_from_text": "[33.2] Our goal is chaos [33.2] We'll build the myth",
        }]}
        fixed = align_proof_timestamps(quiz, WORDS)
        self.assertEqual(fixed, 1)
        self.assertEqual(
            quiz["multiple_choice_questions"][0]["proof_from_text"],
            "[33.2] Our goal is chaos [47.0] We'll build the myth",
        )

    def test_correct_timestamp_is_left_alone(self):
        quiz = {"tfng_questions": [{"proof_from_text": "[33.2] Our goal is chaos"}]}
        self.assertEqual(align_proof_timestamps(quiz, WORDS), 0)
        self.assertEqual(
            quiz["tfng_questions"][0]["proof_from_text"], "[33.2] Our goal is chaos",
        )

    def test_paraphrase_keeps_ai_value(self):
        """So'zlar oqimida topilmasa — yomonlashtirmaymiz, eski qiymat qoladi."""
        quiz = {"fill_gap_questions": [{
            "proof_from_text": "[12.0] something the speaker never actually said",
        }]}
        self.assertEqual(align_proof_timestamps(quiz, WORDS), 0)
        self.assertEqual(
            quiz["fill_gap_questions"][0]["proof_from_text"],
            "[12.0] something the speaker never actually said",
        )

    def test_punctuation_and_case_are_ignored(self):
        quiz = {"multiple_choice_questions": [{
            "proof_from_text": "[10.0] our GOAL, is chaos!",
        }]}
        self.assertEqual(align_proof_timestamps(quiz, WORDS), 1)
        self.assertTrue(
            quiz["multiple_choice_questions"][0]["proof_from_text"].startswith("[33.2]")
        )

    def test_no_words_json_is_a_noop(self):
        quiz = {"multiple_choice_questions": [{"proof_from_text": "[5.0] Our goal is chaos"}]}
        self.assertEqual(align_proof_timestamps(quiz, []), 0)
        self.assertEqual(
            quiz["multiple_choice_questions"][0]["proof_from_text"], "[5.0] Our goal is chaos",
        )

    def test_partial_match_uses_first_words(self):
        """Iqtibos uzunroq bo'lsa ham boshlanish so'zlari bo'yicha topiladi."""
        quiz = {"multiple_choice_questions": [{
            "proof_from_text": "[1.0] We'll build the myth of a strong leader",
        }]}
        self.assertEqual(align_proof_timestamps(quiz, WORDS), 1)
        self.assertTrue(
            quiz["multiple_choice_questions"][0]["proof_from_text"].startswith("[47.0]")
        )

"""IELTS sahifasi shabloni va uni QAYTA yasash.

Muammo: `IeltsListeningTest.html` bazada parse paytida qotib qoladi. Plyerga
yangi imkoniyat qo'shilsa (audio pozitsiyasini surish paneli) ilgari parse
qilingan testlar undan bexabar qoladi — foydalanuvchi "audio boshlash bor,
lekin xohlagan joyiga surish yo'q" deb ko'rgan holat aynan shu.

Yechim: `parts_json` saqlanadi va `rebuild_html()` sahifani JORIY shablon
bilan, **manba saytga murojaat qilmasdan** qayta yasaydi.
"""
from django.test import TestCase

from apps.catalog.ielts_parser import build_html, parts_from_rendered, rebuild_html
from apps.catalog.models import IeltsListeningTest

def _q(numbers: list[int]) -> str:
    """Savol raqamlari HTML'da ham bo'lsin — `parts_from_rendered` shulardan
    savol sonini qayta hisoblaydi (haqiqiy sahifa aynan shunday)."""
    return "".join(
        f'<div class="ielts-listening-question-item">'
        f'<span class="ielts-listening-question-number">{n}</span>'
        f'<input type="text" data-q="{n}"></div>'
        for n in numbers
    )


PARTS = [
    {
        "num": 1,
        "questions": [1, 2, 3],
        "html": _q([1, 2, 3]),
        "audio": "https://example.com/part1.mp3",
    },
    {
        "num": 2,
        "questions": [4, 5],
        "html": _q([4, 5]),
        "audio": "https://example.com/part2.mp3",
    },
]


class BuildHtmlTests(TestCase):
    def test_player_has_a_seek_bar(self):
        """Har part uchun pozitsiya paneli va vaqt ko'rsatkichi bo'lishi shart."""
        html, total = build_html("Sinov testi", PARTS)
        self.assertEqual(total, 5)
        for num in (1, 2):
            self.assertIn(f'data-seek-for="{num}"', html)
            self.assertIn(f'data-time-for="{num}"', html)
            self.assertIn(f'data-target-part="{num}"', html)
        # Surish mantiqi ham sahifada bo'lsin (faqat chizma emas)
        self.assertIn("audio.currentTime = (seekEl.value / 1000) * audio.duration", html)

    def test_player_has_skip_buttons_and_arrow_keys(self):
        """"Ozgina orqaga / ozgina oldinga" — aniq tugmalar + strelkalar.

        Panelni sudrash aniq pozitsiya uchun; kichik tuzatish uchun esa
        −10s / +10s tugmalari va ← → tezroq va aniqroq.
        """
        html, _ = build_html("Sinov", PARTS)
        self.assertIn('data-skip="-10"', html)
        self.assertIn('data-skip="10"', html)
        self.assertIn("function skipBy(delta)", html)
        self.assertIn("ArrowLeft", html)
        self.assertIn("ArrowRight", html)

    def test_no_network_needed(self):
        """`build_html` faqat `parts` dan ishlaydi — URL ham, soup ham kerak emas."""
        html, _ = build_html("", PARTS)
        self.assertIn("part1.mp3", html)
        self.assertIn("IELTS Listening Test", html)  # bo'sh sarlavha uchun default

    def test_missing_audio_is_flagged(self):
        html, _ = build_html("X", [{"num": 1, "questions": [1], "html": "", "audio": ""}])
        self.assertIn("audio topilmadi", html)


class RebuildTests(TestCase):
    def test_rebuild_works_from_the_stored_page_without_network(self):
        """`parts_json` bo'lmasa ham TARMOQSIZ tuzatiladi.

        Eski yozuvlarda `parts_json` yo'q (maydon keyin qo'shilgan), lekin
        saqlangan `html` da hamma narsa bor — audio havolasi, savollar va
        raqamlari. Shu bois manba saytga (engnovate.com) murojaat qilish
        SHART EMAS: sahifani o'zidan o'qib olamiz.
        """
        old_page, _ = build_html("Eski", PARTS)
        # Eski shablonni taqlid qilamiz: surish paneli olib tashlangan.
        old_page = old_page.replace("audio-seek", "audio-OLD")
        test = IeltsListeningTest.objects.create(
            source_url="https://example.invalid/yoq",   # tarmoq ishlatilmasin
            title="Eski", html=old_page, parts_json=[],
        )

        self.assertTrue(rebuild_html(test))
        test.refresh_from_db()
        self.assertIn("audio-seek", test.html)
        self.assertIn("part1.mp3", test.html)
        self.assertIn("part2.mp3", test.html)
        self.assertEqual(test.total_questions, 5)
        # Keyingi safar eng tez yo'l ishlashi uchun xom partlar saqlanadi
        self.assertEqual(len(test.parts_json), 2)
        self.assertEqual([p['questions'] for p in test.parts_json], [[1, 2, 3], [4, 5]])

    def test_stale_page_is_refreshed_from_parts(self):
        test = IeltsListeningTest.objects.create(
            source_url="https://example.com/test-1",
            title="Eski test",
            html="<html>ESKI SAHIFA — surish paneli yo'q</html>",
            parts_json=PARTS,
            total_questions=5,
        )
        self.assertNotIn("audio-seek", test.html)

        self.assertTrue(rebuild_html(test))
        test.refresh_from_db()
        self.assertIn("audio-seek", test.html)
        self.assertIn("part1.mp3", test.html)
        self.assertEqual(test.total_questions, 5)

    def test_answers_are_never_touched(self):
        answers = {"1": ["Monday"], "2": ["blue"]}
        test = IeltsListeningTest.objects.create(
            source_url="https://example.com/test-2",
            title="Javobli test", html="<html>eski</html>",
            parts_json=PARTS, total_questions=5, answers=answers,
        )
        rebuild_html(test)
        test.refresh_from_db()
        self.assertEqual(test.answers, answers)

    def test_command_reports_without_apply(self):
        """`--apply` siz baza o'zgarmaydi (dry-run)."""
        from io import StringIO

        from django.core.management import call_command

        test = IeltsListeningTest.objects.create(
            source_url="https://example.com/test-3",
            title="Dry run", html="<html>eski</html>", parts_json=PARTS,
        )
        out = StringIO()
        call_command("rebuild_ielts_html", stdout=out)
        test.refresh_from_db()
        self.assertEqual(test.html, "<html>eski</html>")
        self.assertIn("qayta yasaladi", out.getvalue())

        out = StringIO()
        call_command("rebuild_ielts_html", "--apply", stdout=out)
        test.refresh_from_db()
        self.assertIn("audio-seek", test.html)

    def test_already_new_pages_are_skipped(self):
        fresh_html, _ = build_html("Yangi", PARTS)
        test = IeltsListeningTest.objects.create(
            source_url="https://example.com/test-4",
            title="Yangi", html=fresh_html, parts_json=PARTS,
        )
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command("rebuild_ielts_html", "--apply", stdout=out)
        self.assertIn("o'tkazildi", out.getvalue())
        test.refresh_from_db()
        self.assertIn("audio-seek", test.html)

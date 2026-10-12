"""VBR mp3 → CBR (`audio_cbr.py`) — bo'laklar aniq joyidan yangrashi uchun.

Haqiqiy ffmpeg bilan ishlaydi (backend image'ida bor); topilmasa testlar
o'tkazib yuboriladi.
"""
import shutil
import subprocess
import tempfile
from io import StringIO
from pathlib import Path
from unittest import skipUnless

from django.core.management import call_command
from django.test import TestCase, override_settings

from apps.catalog.audio_cbr import CBR_SUFFIX, ensure_cbr, needs_cbr
from apps.catalog.models import AIJob, Dictation

FFMPEG = shutil.which("ffmpeg")
BODY = [{"start_ms": 0, "end_ms": 1000, "text": "Hi.", "fixed": True}]


def make_mp3(path: Path, *, vbr: bool) -> Path:
    """3 s sinus — `-q:a` VBR (Xing sarlavhasi bilan), `-b:a` CBR."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rate = ["-q:a", "4"] if vbr else ["-b:a", "96k"]
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "sine=frequency=440:duration=3", "-c:a", "libmp3lame", *rate, str(path)],
                   check=True)
    return path


@skipUnless(FFMPEG, "ffmpeg yo'q")
class AudioCbrTests(TestCase):
    def setUp(self):
        self.media = Path(tempfile.mkdtemp(prefix="cbr-test-"))
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=str(self.media))
        override.enable()
        self.addCleanup(override.disable)

    def lesson(self, rel: str, *, vbr: bool, **extra) -> Dictation:
        make_mp3(self.media / rel, vbr=vbr)
        d = Dictation(title=f"Lesson {rel}", slug=f"lesson-{Path(rel).stem}", body=BODY, is_published=True,
                      transcription_status=Dictation.TranscriptionStatus.DONE, **extra)
        d.audio.name = rel
        Dictation.objects.bulk_create([d])  # signal'siz — AI navbati umuman tug'ilmasin
        return Dictation.objects.get(title=f"Lesson {rel}")

    def test_detects_vbr_only(self):
        self.assertTrue(needs_cbr(make_mp3(self.media / "a.mp3", vbr=True)))
        self.assertFalse(needs_cbr(make_mp3(self.media / "b.mp3", vbr=False)))
        self.assertFalse(needs_cbr(self.media / "missing.mp3"))
        (self.media / "c.wav").write_bytes(b"RIFF")
        self.assertFalse(needs_cbr(self.media / "c.wav"))

    def test_converts_to_new_name_and_removes_old(self):
        d = self.lesson("dictations/audio/x/1-num.mp3", vbr=True)
        old = Path(d.audio.path)
        self.assertTrue(ensure_cbr(d))
        d.refresh_from_db()
        # Yangi nom — eski URL nginx/Cloudflare keshida qolgan bo'lsa ham to'qnashmaydi.
        self.assertEqual(d.audio.name, "dictations/audio/x/1-num" + CBR_SUFFIX)
        self.assertTrue(Path(d.audio.path).exists())
        self.assertFalse(old.exists())
        self.assertFalse(needs_cbr(d.audio.path))
        self.assertFalse(ensure_cbr(d))  # ikkinchi marta — hech narsa qilmaydi
        self.assertFalse(AIJob.objects.exists())  # post_save ishlamadi, token sarflanmadi

    def test_cbr_file_is_left_alone(self):
        d = self.lesson("dictations/audio/x/2-cbr.mp3", vbr=False)
        self.assertFalse(ensure_cbr(d))
        d.refresh_from_db()
        self.assertEqual(d.audio.name, "dictations/audio/x/2-cbr.mp3")

    def test_command_dry_run_then_apply(self):
        vbr = self.lesson("dictations/audio/x/3-a.mp3", vbr=True, source_url="https://dd/3")
        own = self.lesson("dictations/audio/x/4-b.mp3", vbr=True)
        out = StringIO()
        call_command("make_audio_cbr", stdout=out)
        self.assertIn("VBR/ABR (o'tkaziladi): 2", out.getvalue())
        vbr.refresh_from_db()
        self.assertTrue(vbr.audio.name.endswith("3-a.mp3"))  # dry-run tegmaydi

        call_command("make_audio_cbr", "--apply", "--only-imported", stdout=StringIO())
        vbr.refresh_from_db(); own.refresh_from_db()
        self.assertTrue(vbr.audio.name.endswith(CBR_SUFFIX))
        self.assertTrue(own.audio.name.endswith("4-b.mp3"))  # o'zimizniki — filtrdan tashqarida

    def test_scraper_converts_downloaded_audio(self):
        from apps.catalog.management.commands.scrape_dailydictation import to_cbr_rel
        make_mp3(self.media / "dictations/audio/dailydictation/number/5-n.mp3", vbr=True)
        rel = to_cbr_rel("dictations/audio/dailydictation/number/5-n.mp3")
        self.assertEqual(rel, "dictations/audio/dailydictation/number/5-n" + CBR_SUFFIX)
        self.assertTrue((self.media / rel).exists())
        self.assertFalse((self.media / "dictations/audio/dailydictation/number/5-n.mp3").exists())

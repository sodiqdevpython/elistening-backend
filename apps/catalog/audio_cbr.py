"""MP3'ni doimiy bitreytga (CBR) o'tkazish — diktant bo'lagi AYNAN o'z joyidan yangrasin.

**Muammo (brauzerda o'lchangan, 2026-10-12).** Diktant bitta uzun faylni
`currentTime = start` bilan surib, bo'lak-bo'lak ijro qiladi. O'zgaruvchan
bitreytli (VBR/ABR) MP3'da Chrome aniq joyga sakray olmaydi — faylning Xing
jadvali bo'yicha TAXMIN qiladi. Numbers (3) darsida (310 s, LAME `--preset 64`):

    so'ralgan   1.71 s → haqiqatda   1.708 s   (−2 ms)
    so'ralgan 120.50 s → haqiqatda 120.103 s   (−0.4 s)
    so'ralgan 241.64 s → haqiqatda 240.505 s   (−1.1 s)

Ya'ni bo'lak boshida OLDINGI raqam eshitiladi, oxiri esa kesiladi — foydalanuvchi
buni "margin" deb ko'rdi. O'sha faylning CBR nusxasida xato ±36 ms (bir MP3
kadridan kam): CBR'da joy baytdan aniq hisoblanadi.

dailydictation'ning o'zida bu muammo yo'q, chunki u har gapni ALOHIDA qisqa
faylda beradi; bizda esa bitta fayl — shu bois uni CBR qilamiz.

**Yangi nom bilan yoziladi** (`<nom>.cbr.mp3`), eski fayl o'chiriladi: media
nginx'da `expires 7d` va Cloudflare'da keshlanadi — o'sha nomda almashtirilsa,
foydalanuvchilar bir hafta eski (VBR) faylni eshitaverardi.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

CBR_SUFFIX = ".cbr.mp3"
# Nutq uchun yetarli, manba sifatini (≈64–100 kbps VBR) tushirmaydi.
BITRATE_MONO = "96k"
BITRATE_STEREO = "128k"


class AudioConvertError(RuntimeError):
    pass


def needs_cbr(path: str | os.PathLike) -> bool:
    """O'zgaruvchan bitreytli MP3mi (VBR yoki ABR)? Boshqa formatlar — yo'q.

    WAV/M4A brauzerda aniq suriladi; Xing sarlavhasisiz MP3'ni mutagen
    `UNKNOWN` deydi — u odatda CBR, tegmaymiz.
    """
    path = Path(path)
    if path.suffix.lower() != ".mp3" or not path.exists():
        return False
    try:
        from mutagen.mp3 import MP3, BitrateMode
        return MP3(str(path)).info.bitrate_mode in (BitrateMode.VBR, BitrateMode.ABR)
    except Exception:
        return False


def cbr_name(path: str | os.PathLike) -> Path:
    path = Path(path)
    stem = path.name[: -len(CBR_SUFFIX)] if path.name.endswith(CBR_SUFFIX) else path.stem
    return path.with_name(stem + CBR_SUFFIX)


def convert_to_cbr(src: str | os.PathLike) -> Path:
    """`src` ning CBR nusxasini yonida yaratadi va yo'lini qaytaradi.

    Avval vaqtinchalik faylga yoziladi, keyin `os.replace` — yarim yozilgan
    fayl hech qachon ko'rinmaydi. `src` ga tegilmaydi (o'chirish chaqiruvchida).
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise AudioConvertError("ffmpeg topilmadi (backend image'ida bo'lishi kerak).")
    src = Path(src)
    channels = 1
    try:
        from mutagen.mp3 import MP3
        channels = MP3(str(src)).info.channels or 1
    except Exception:
        pass
    out = cbr_name(src)
    tmp = out.with_name(out.name + ".part")
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-i", str(src), "-map", "0:a:0", "-vn",
        "-c:a", "libmp3lame", "-b:a", BITRATE_MONO if channels == 1 else BITRATE_STEREO,
        "-f", "mp3", str(tmp),
    ]
    try:
        run = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if run.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
            raise AudioConvertError((run.stderr or "ffmpeg xatosi").strip()[-300:])
        os.replace(tmp, out)
    finally:
        tmp.unlink(missing_ok=True)
    return out


def ensure_cbr(dictation) -> bool:
    """Diktant audiosi VBR bo'lsa CBR'ga o'tkazadi → `True` (o'zgardi).

    Bazaga `QuerySet.update` bilan yoziladi — `post_save` signali (Whisper/Haiku
    navbati) ishlamaydi, ya'ni token sarflanmaydi.
    """
    if not dictation.audio:
        return False
    try:
        src = Path(dictation.audio.path)
    except Exception:
        return False
    if not needs_cbr(src):
        return False
    store_cbr(dictation, src, convert_to_cbr(src))
    return True


def store_cbr(dictation, src: Path, out: Path) -> None:
    """Tayyor CBR faylni diktantga yozadi va eskisini o'chiradi (faqat bazaga
    tegadigan qism — `make_audio_cbr` ffmpeg'ni oqimlarda, buni esa asosiy
    oqimda chaqiradi)."""
    new_name = str(Path(dictation.audio.name).with_name(out.name)).replace("\\", "/")
    type(dictation).objects.filter(pk=dictation.pk).update(audio=new_name)
    dictation.audio.name = new_name
    if out != src:
        src.unlink(missing_ok=True)
    logger.info("Audio CBR'ga o'tkazildi: %s", new_name)

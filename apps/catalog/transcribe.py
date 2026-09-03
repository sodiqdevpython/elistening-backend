"""OpenAI Whisper orqali audio → transkript.

Namuna: `ai/audio.py`. Bitta chaqiruv, retry yo'q — token tugagan yoki
tarmoq xatosi bo'lsa xato darrov chiqariladi va admin panelida ko'rinadi.

Ikki manba qo'llaniladi:
  1. `Dictation.audio` — yuklangan mp3/wav (saqlanadi)
  2. `Dictation.youtube_link` — yt-dlp bilan vaqtinchalik yuklab olinadi
     va transkriptdan keyin darrov o'chiriladi (saqlanmaydi).
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

from django.conf import settings

from .models import Dictation

logger = logging.getLogger(__name__)

# Whisper API cheklovlari va bizning qo'shimcha cheklovlarimiz.
MAX_DURATION_SEC = 20 * 60   # 20 daqiqa
MIN_DURATION_SEC = 5         # 5 soniya
MAX_FILE_SIZE_MB = 25        # OpenAI Whisper cheklovi


class TranscriptionError(Exception):
    """Ko'rinadigan xato — admin xabarnomasiga chiqadi."""


def measure_audio_duration(path: str | Path) -> int:
    """Audio faylning davomiyligini soniyada qaytaradi (mutagen)."""
    from mutagen import File as MutagenFile
    audio = MutagenFile(str(path))
    if audio is None or not audio.info:
        raise TranscriptionError("Audio faylni o'qib bo'lmadi (format tanilmadi).")
    return int(round(audio.info.length))


def _get_client():
    """OpenAI clientini `.env` dagi GPT_API_KEY bilan yaratadi.

    Timeout 30 daqiqa — uzun audiolar uchun (20 daqiqalik audio Whisper'da
    bir necha daqiqa ishlanadi). Retry yo'q (max_retries=0) — token tejash.
    """
    from openai import OpenAI
    api_key = getattr(settings, "GPT_API_KEY", "") or os.environ.get("GPT_API_KEY", "")
    if not api_key:
        raise TranscriptionError(
            "GPT_API_KEY topilmadi. backend/.env fayliga qo'shing."
        )
    return OpenAI(api_key=api_key, timeout=30 * 60, max_retries=0)


def _chunks_from_segments(segments: list[dict]) -> list[dict]:
    """Whisper segmentlaridan diktant body chunklarini yasaydi."""
    return [
        {
            "start_ms": int(round(float(s["start"]) * 1000)),
            "end_ms": int(round(float(s["end"]) * 1000)),
            "text": (s["text"] or "").strip(),
        }
        for s in segments
        if (s.get("text") or "").strip()
    ]


@contextmanager
def _prepare_audio(dictation: Dictation):
    """Diktantdan Whisper'ga yuboriladigan audio yo'lini yield qiladi.

    - `audio` bor bo'lsa: o'zining yo'li (o'zgarmaydi, saqlanadi).
    - `youtube_link` bor bo'lsa: yt-dlp bilan vaqtinchalik faylga yuklab
      olinadi va kontekst tugagach o'chiriladi (fayl saqlanmaydi).
    - Ikkalasi ham yo'q: xato.
    """
    if dictation.audio:
        audio_path = Path(dictation.audio.path)
        if not audio_path.exists():
            raise TranscriptionError(f"Audio fayl topilmadi: {audio_path.name}")
        yield audio_path
        return

    if dictation.youtube_link:
        with _download_youtube_audio(dictation.youtube_link) as tmp_path:
            yield tmp_path
        return

    raise TranscriptionError(
        "Audio ham, YouTube havolasi ham berilmagan. Kamida bittasini qo'shing."
    )


CEFR_LEVELS = ("A1", "A2", "B1", "B2", "C1", "C2")


def detect_cefr_level(text: str) -> str:
    """Transkript matnini tahlil qilib CEFR darajasini aniqlaydi (A1..C2).

    Whisper full_text'ini OpenAI chat modeliga yuboradi va u lug'at, gap
    tuzilishi va murakkabligiga qarab bitta CEFR yorlig'ini qaytaradi.
    Xato bo'lsa (kvota, tarmoq, format) bo'sh string qaytaradi — chaqiruvchi
    fallback default'ni qo'llasa yaxshi bo'ladi. Admin qo'lda o'zgartira oladi.
    """
    text = (text or "").strip()
    if len(text) < 20:
        return ""
    try:
        client = _get_client()
    except TranscriptionError:
        return ""
    # Rubrikali prompt — model B1/B2 ga "xavfsiz" default bermasligi uchun har
    # darajaning aniq belgilari beriladi. Faqat yorliq qaytariladi.
    prompt = (
        "You are a strict CEFR grader. Read the WHOLE text and label the "
        "difficulty of the ENGLISH ITSELF (not the topic) with exactly ONE of: "
        "A1 A2 B1 B2 C1 C2. Output only the label — no punctuation, no other words.\n"
        "Do NOT default to B1 or B2. Judge by the language that RECURS through "
        "the text, weighing vocabulary frequency/abstractness/idiom, the range "
        "of tenses and structures, and sentence complexity.\n"
        "A1 = very high-frequency everyday words, present simple, very short "
        "simple sentences, concrete topics.\n"
        "A2 = common everyday vocab, past/future, short joined sentences, "
        "familiar concrete topics.\n"
        "B1 = everyday + some topic words, a range of tenses, some complex "
        "sentences, simple opinions.\n"
        "B2 = broad vocab incl. some abstract/idiomatic, full tense range + "
        "passives/conditionals, long multi-clause sentences, argument.\n"
        "C1 = wide precise vocab with idioms and nuance, complex embedded "
        "structures, abstract/specialised, implied meaning.\n"
        "C2 = sophisticated, highly idiomatic/figurative, dense structure, "
        "highly abstract/technical/nuanced.\n"
        "Simple slow everyday content is A1/A2; dense abstract idiomatic content "
        "is C1/C2. Different texts must get different labels.\n\n"
        "Text:\n" + text[:6000]
    )
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=4,
        )
        raw = (resp.choices[0].message.content or "").strip().upper()
        # Modelning kutilmagan javobidan darajani ajratib olamiz
        for lvl in CEFR_LEVELS:
            if lvl in raw:
                return lvl
        return ""
    except Exception as exc:
        logger.warning("detect_cefr_level failed: %s", exc)
        return ""


def fetch_youtube_title(url: str) -> str:
    """YouTube URL dan video sarlavhasini oladi (TEZ — oEmbed orqali).

    Admin panelida video-asosli diktant yaratganda user sarlavhani qo'lda
    yozmasin uchun: `save_model` bu funksiyani chaqiradi.

    MUHIM: ilgari `yt_dlp.extract_info` ishlatilardi — u YouTube sahifasini
    TO'LIQ yuklab parse qiladi (5-30s+, ba'zan rate-limit bilan osilib qoladi)
    va admin saqlashni SHUNCHA bloklardi. Endi **oEmbed** — bitta yengil HTTP
    GET (~200ms, 4s qattiq timeout). Xato bo'lsa bo'sh string (chaqiruvchi
    fallback qo'yadi). Slow yt-dlp sync yo'lda umuman ishlatilmaydi.
    """
    if not url:
        return ""
    import json as _json
    import urllib.parse
    import urllib.request

    oembed = ("https://www.youtube.com/oembed?url="
              + urllib.parse.quote(url, safe="") + "&format=json")
    try:
        req = urllib.request.Request(oembed, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
        return (data.get("title") or "").strip()
    except Exception as exc:
        logger.warning("fetch_youtube_title (oembed) failed for %s: %s", url, exc)
        return ""


@contextmanager
def _download_youtube_audio(url: str):
    """yt-dlp orqali YouTube'dan mp3 audio yuklab oladi (vaqtinchalik).

    Kontekst tugashi bilan hamma fayllar o'chiriladi — asosiy DB'ga
    yozilmaydi, media/'ga saqlanmaydi. Whisper'ga faqat vaqtinchalik path.
    """
    try:
        import yt_dlp
    except ImportError as exc:
        raise TranscriptionError(
            "yt-dlp o'rnatilmagan. `pip install yt-dlp` qiling."
        ) from exc

    tmp_dir = tempfile.mkdtemp(prefix="whisper-yt-")
    try:
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": os.path.join(tmp_dir, "audio.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "128",
            }],
            "noplaylist": True,
            # VPS (data-markaz IP) dan YouTube standart `web` mijozini bloklaydi
            # → "HTTP Error 403: Forbidden". Boshqa mijozlarni navbat bilan
            # sinaymiz (tv/web_safari/mweb/... ko'pincha 403'ni chetlab o'tadi).
            "extractor_args": {
                "youtube": {
                    "player_client": ["tv", "web_safari", "mweb", "android", "ios", "web"],
                },
            },
            "retries": 3,
            "fragment_retries": 3,
        }
        # Agar player_client ham yordam bermasa — cookie fayl (eng ishonchli).
        # `YTDLP_COOKIES=/app/cookies.txt` env berilsa va fayl mavjud bo'lsa ishlatiladi.
        _cookies = (os.environ.get("YTDLP_COOKIES") or "").strip()
        if _cookies and os.path.exists(_cookies):
            ydl_opts["cookiefile"] = _cookies
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
        except yt_dlp.utils.DownloadError as exc:
            msg = str(exc)
            low = msg.lower()
            if "unavailable" in low:
                friendly = "YouTube video ochiq emas yoki o'chirilgan."
            elif "private" in low:
                friendly = "YouTube video xususiy (private)."
            elif "sign in" in low or "age" in low:
                friendly = "YouTube video yosh cheklovi bilan — yuklab bo'lmadi."
            elif "http error 429" in low or "too many requests" in low:
                friendly = "YouTube rate limit — biroz kutib qayta urining."
            else:
                friendly = f"YouTube'dan yuklab bo'lmadi: {msg[:300]}"
            raise TranscriptionError(friendly) from exc
        except Exception as exc:
            raise TranscriptionError(
                f"yt-dlp xatosi: {str(exc)[:300]}"
            ) from exc

        # Yuklangan audioni topamiz
        files = list(Path(tmp_dir).glob("audio.*"))
        if not files:
            raise TranscriptionError("YouTube audio yuklanmadi (fayl topilmadi).")
        yield files[0]
    finally:
        # Vaqtinchalik faylni albatta o'chirib qo'yamiz — saqlanmaydi.
        shutil.rmtree(tmp_dir, ignore_errors=True)


def transcribe_dictation(dictation: Dictation, *, language: str = "en") -> Dictation:
    """Dictation audio yoki youtube_link'ni Whisper orqali transkript qiladi.

    Xato bo'lsa `TranscriptionError` ko'tariladi. Retry yo'q — token tejash uchun.
    """
    if not dictation.audio and not dictation.youtube_link:
        raise TranscriptionError(
            "Audio yoki YouTube havolasi berilmagan. Kamida bittasini qo'shing."
        )

    # Statusni "processing" ga o'tkazamiz.
    dictation.transcription_status = Dictation.TranscriptionStatus.PROCESSING
    dictation.transcription_error = ""
    dictation.save(update_fields=[
        "transcription_status", "transcription_error", "updated_at",
    ])

    try:
        with _prepare_audio(dictation) as audio_path:
            # Hajm cheklovi (OpenAI Whisper 25 MB).
            size_mb = audio_path.stat().st_size / (1024 * 1024)
            if size_mb > MAX_FILE_SIZE_MB:
                raise TranscriptionError(
                    f"Audio hajmi katta: {size_mb:.1f} MB. Cheklov {MAX_FILE_SIZE_MB} MB."
                )

            # Davomiylik cheklovi (5s — 20 daq).
            try:
                duration = measure_audio_duration(audio_path)
            except TranscriptionError:
                raise
            except Exception as exc:
                raise TranscriptionError(f"Audio davomiyligini o'lchab bo'lmadi: {exc}") from exc

            if duration < MIN_DURATION_SEC:
                raise TranscriptionError(
                    f"Audio juda qisqa: {duration}s (kamida {MIN_DURATION_SEC}s bo'lishi kerak)."
                )
            if duration > MAX_DURATION_SEC:
                raise TranscriptionError(
                    f"Audio juda uzun: {duration // 60}:{duration % 60:02d} "
                    f"(cheklov {MAX_DURATION_SEC // 60} daqiqa)."
                )

            # Davomiylikni modelga yozamiz (audio saqlangan bo'lsa foydali).
            dictation.audio_duration_sec = duration
            dictation.save(update_fields=["audio_duration_sec", "updated_at"])

            # OpenAI Whisper chaqiruvi — bitta urinish, retry yo'q.
            client = _get_client()
            with audio_path.open("rb") as f:
                result = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=f,
                    language=language,
                    response_format="verbose_json",
                    timestamp_granularities=["word", "segment"],
                )
    except TranscriptionError as exc:
        _mark_failed(dictation, str(exc))
        raise
    except Exception as exc:
        friendly = _friendly_openai_error(str(exc))
        _mark_failed(dictation, friendly)
        raise TranscriptionError(friendly) from exc

    # Natijani modelga yozamiz.
    try:
        segments = [
            {"start": seg.start, "end": seg.end, "text": (seg.text or "").strip()}
            for seg in (result.segments or [])
        ]
        words = [
            {"start": w.start, "end": w.end, "word": (w.word or "").strip()}
            for w in (result.words or [])
        ]
        dictation.full_text = (result.text or "").strip()
        dictation.words_json = words
        dictation.body = _chunks_from_segments(segments)
        dictation.transcription_status = Dictation.TranscriptionStatus.DONE
        dictation.transcription_error = ""
        dictation.is_published = True   # sayt darrov ko'rsin

        update_fields = [
            "full_text", "words_json", "body",
            "transcription_status", "transcription_error", "is_published", "updated_at",
        ]

        # CEFR darajasi bo'sh bo'lsa AI aniqlab beradi. Admin qo'lda o'rnatgan
        # darajani ustidan yozmaymiz — foydalanuvchi tanlovini hurmat qilamiz.
        if not (dictation.cefr_level or "").strip():
            detected = detect_cefr_level(dictation.full_text)
            if detected:
                dictation.cefr_level = detected
                update_fields.append("cefr_level")

        dictation.save(update_fields=update_fields)
    except Exception as exc:
        _mark_failed(dictation, f"Natijani saqlashda xato: {exc}")
        raise TranscriptionError(f"Natijani saqlashda xato: {exc}") from exc

    return dictation


def _friendly_openai_error(msg: str) -> str:
    low = msg.lower()
    if "insufficient_quota" in low or "quota" in low or "billing" in low:
        return "OpenAI kvota tugagan yoki hisob to'lanmagan."
    if "invalid_api_key" in low or "authentication" in low or "401" in msg:
        return "GPT_API_KEY noto'g'ri yoki muddati o'tgan."
    if "rate limit" in low or "429" in msg:
        return "OpenAI rate limit — biroz kutib qayta urining."
    if "timeout" in low or "connection" in low:
        return "Tarmoq xatosi — internetni tekshiring."
    return f"OpenAI xatosi: {msg[:400]}"


def _mark_failed(dictation: Dictation, message: str) -> None:
    """Xato holatida modelga xato xabarini saqlaydi."""
    try:
        dictation.transcription_status = Dictation.TranscriptionStatus.FAILED
        dictation.transcription_error = message[:2000]
        dictation.save(update_fields=[
            "transcription_status", "transcription_error", "updated_at",
        ])
    except Exception as exc:
        logger.exception("mark_failed failed: %s", exc)

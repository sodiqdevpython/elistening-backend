"""Shorts uchun AI pipeline.

Admin faqat `youtube_link` beradi — bu modul qolgan hamma narsani qiladi:

1. **yt-dlp** — YouTube dan vaqtinchalik audio yuklab oladi va meta ma'lumot
   (title, duration) ni beradi. Audio saqlanmaydi, faqat transkript uchun.
2. **OpenAI Whisper** — audio → to'liq matn + so'z-darajasidagi timestamp.
3. **Claude Haiku** — timestamp bilan formatlangan matnni `prompt_shorts.txt`
   bilan tahlil qiladi va JSON qaytaradi (CEFR, teglar, MCQ, TFNG savollar).
4. Natija `Short` modeliga saqlanadi.

Xato bo'lsa retry yo'q — token tejash uchun. `TranscriptionError` chiqadi
va admin panelida darrov ko'rinadi.
"""
from __future__ import annotations

import json
import logging
import os
import re

from django.conf import settings

from . import mock_data
from .models import Short
from .transcribe import (
    MAX_DURATION_SEC, MAX_FILE_SIZE_MB, MIN_DURATION_SEC,
    TranscriptionError, _download_youtube_audio, _friendly_openai_error,
    _get_client, fetch_youtube_title, measure_audio_duration,
)

logger = logging.getLogger(__name__)


# --- Yordamchi ---------------------------------------------------------------

_SENTENCE_END = ('.', '!', '?', '…')


def _ai_prompt_candidates(name: str) -> list[str]:
    """AI prompt fayli qidiriladigan joylar (birinchi topilgani ishlatiladi).

    Dev'da repo ildizidagi `ai/`, docker'da esa `/app/ai` (compose mount)
    yoki env `AI_PROMPT_DIR`. Bir manba (`ai/` papkasi) turli joylashuvda
    ishlaydi — nusxa saqlamaymiz."""
    from pathlib import Path

    from django.conf import settings

    cands: list[str] = []
    env_dir = os.environ.get("AI_PROMPT_DIR")
    if env_dir:
        cands.append(os.path.join(env_dir, name))
    base = Path(settings.BASE_DIR)
    # `aiprompts/` — backend repo ICHIDA, git-tracked (`.gitignore` `/ai/*.txt`
    # ni bloklaydi, shu bois promptlar shu papkada yashaydi va `git pull` bilan
    # keladi). Dockerfile `COPY . .` uni `/app/aiprompts` ga oladi.
    cands.append(str(base / "aiprompts" / name))
    cands.append(str(base / "ai" / name))          # /app/ai (docker mount) yoki backend/ai
    cands.append(str(base.parent / "ai" / name))   # repo_root/ai (dev)
    cands.append(os.path.join("/ai", name))         # legacy absolute
    return cands


def _read_ai_prompt(name: str) -> str:
    """AI prompt faylini o'qiydi. Topilmasa qidirilgan yo'llarni ko'rsatib
    aniq xato beradi (admin darrov tushunadi)."""
    tried: list[str] = []
    for path in _ai_prompt_candidates(name):
        tried.append(path)
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except OSError:
            continue
    raise FileNotFoundError(
        f"{name} topilmadi. Qidirilgan joylar: {tried}. "
        f"Docker'da `./ai` papkasini `/app/ai` ga mount qiling yoki "
        f"`AI_PROMPT_DIR` env bering."
    )


# --- Savollar ketma-ketligi (IELTS tartibi) ---------------------------------
#
# Iqtibosni `[12.3] matn` bo'laklariga ajratish (guruh — vaqtning o'zi).
_PROOF_SPLIT_RE = re.compile(r"\[\s*([0-9]+(?:\.[0-9]+)?)\s*\]")
# Iqtibosni so'zlar oqimidan topish uchun kerakli minimal so'z soni.
_MIN_MATCH = 4

# Claude promptda "timestamp bo'yicha o'sish tartibida ber" deb so'ralgan,
# lekin LLM buni HAR DOIM ham bajarmaydi. Foydalanuvchi videoni bir marta
# boshidan oxirigacha ko'radi — savollar esa aynan shu tartibda kelishi kerak
# (IELTS Listening qoidasi): 1-savolning isboti eng erta, 2-niki keyinroq...
#
# Shu bois AI natijasini serverda MAJBURAN saralaymiz — bu deterministik va
# tokenga ham tushmaydi.

_PROOF_TS_RE = re.compile(r"\[\s*([0-9]+(?:\.[0-9]+)?)\s*\]")


def _proof_seconds(question: dict) -> float | None:
    """`proof_from_text` ichidagi birinchi `[12.3]` timestamp — soniyada.

    Timestamp bo'lmasa `None` (masalan TFNG "Not given" — isbot bo'lmaydi).
    """
    match = _PROOF_TS_RE.search((question or {}).get("proof_from_text") or "")
    return float(match.group(1)) if match else None


def _sort_by_proof(questions: list) -> list:
    """Bitta ro'yxatni isbot vaqti bo'yicha o'sish tartibida qaytaradi.

    Timestamp'siz savollar ("Not given") oldingi savolning vaqtini meros olib
    o'z qo'shnisi yonida qoladi — shunda AI qo'ygan mantiqiy joy buzilmaydi,
    lekin ketma-ketlik baribir monoton bo'ladi. Saralash barqaror (stable):
    bir xil vaqtli savollar AI bergan tartibda qoladi.
    """
    if not isinstance(questions, list):
        return questions
    keyed: list[tuple[float, int, dict]] = []
    carry = 0.0
    for i, q in enumerate(questions):
        sec = _proof_seconds(q) if isinstance(q, dict) else None
        if sec is None:
            sec = carry            # isbotsiz savol — qo'shnisi bilan birga yuradi
        else:
            carry = sec
        keyed.append((sec, i, q))
    keyed.sort(key=lambda item: (item[0], item[1]))
    return [q for _, _, q in keyed]


# Frontend savol raqamini SHU tartibda hisoblaydi: avval MCQ, keyin TFNG,
# oxirida Fill-gap. Har bo'lim uchun bir nechta kalit bo'lishi mumkin (AI
# ba'zan `true_false_questions` deb yozadi).
_SECTIONS: tuple[tuple[str, ...], ...] = (
    ("multiple_choice_questions",),
    ("tfng_questions", "true_false_questions"),
    ("fill_gap_questions",),
)


def _display_sequence(quiz: dict) -> list[dict]:
    """Foydalanuvchi ko'radigan YAGONA ro'yxat (MCQ → TFNG → Fill)."""
    out: list[dict] = []
    for keys in _SECTIONS:
        for key in keys:
            lst = quiz.get(key)
            if isinstance(lst, list):
                out.extend(q for q in lst if isinstance(q, dict))
                break
    return out


def _effective_seconds(questions: list[dict]) -> list[float]:
    """Har savolning isbot vaqti; isbotsizi ("Not given") — oldingisiniki.

    Shunda timestampsiz savol qo'shnisidan ajralib ketmaydi, lekin ketma-ketlik
    baribir monoton bo'ladi.
    """
    out: list[float] = []
    carry = 0.0
    for q in questions:
        sec = _proof_seconds(q)
        if sec is None:
            sec = carry
        else:
            carry = sec
        out.append(sec)
    return out


def sequence_is_chronological(quiz: dict) -> bool:
    """Yagona ro'yxat (MCQ → TFNG → Fill) video bo'yicha oldinga yuradimi?

    Ya'ni AI 5-qoidani bajardimi. Faqat tekshiradi — `_order_quiz` uni
    baribir majburan to'g'rilaydi.
    """
    secs = _effective_seconds(_display_sequence(quiz))
    return all(a <= b for a, b in zip(secs, secs[1:]))


def _number_globally(quiz: dict) -> None:
    """Har savolga `number` — VIDEO bo'yicha xronologik o'rin (1..N).

    ## Nega raqam serverda hisoblanadi

    Ilgari raqam mijozda pozitsiyadan chiqarilardi: MCQ 1..M, keyin TFNG
    M+1.., keyin Fill. Har bo'lim esa videoni BOSHIDAN oxirigacha alohida
    bosib o'tardi, ya'ni 2-savol (oxirgi MCQ) 90-soniyada, 3-savol (birinchi
    TFNG) 8-soniyada bo'lishi mumkin edi. Foydalanuvchi buni aynan shunday
    ko'rdi: *"oldin 3, 1, 2, 4 shu tartibda javob kelayabdi"*.

    Prompt endi bitta o'tishni talab qiladi (`ai/prompt_shorts.txt`, 5-qoida),
    lekin LLM'ga TAYANIB bo'lmaydi. Shu bois raqam bu yerda, isbot vaqti
    bo'yicha beriladi: **1-savolning javobi eng erta eshitiladi, keyin 2-niki**
    — modeldan qat'i nazar, 100%.

    Tartib barqaror (stable): bir xil vaqtli savollar ko'rsatish tartibida
    qoladi. Mijozlar `q.number` ni ishlatadi (bo'lmasa — eski pozitsiya).
    """
    seq = _display_sequence(quiz)
    secs = _effective_seconds(seq)
    order = sorted(range(len(seq)), key=lambda i: (secs[i], i))
    for rank, idx in enumerate(order, start=1):
        seq[idx]["number"] = rank


def _order_quiz(quiz: dict) -> dict:
    """Savollarni ketma-ketlikka soladi — IKKI bosqichda.

    1. **Har ro'yxat ichida** isbot vaqti bo'yicha saralanadi.
    2. **Butun ro'yxat bo'ylab** xronologik `number` qo'yiladi (bo'limlar
       vaqt bo'yicha ustma-ust tushib qolsa ham raqam to'g'ri bo'ladi).
    """
    for key in ("multiple_choice_questions", "tfng_questions",
                "true_false_questions", "fill_gap_questions"):
        if isinstance(quiz.get(key), list):
            quiz[key] = _sort_by_proof(quiz[key])
    _number_globally(quiz)
    return quiz


# Apostrof — so'z ICHIDA, ajratuvchi emas. Ikkala tomonda bir xil o'chiriladi,
# aks holda `We'll` iqtibosda `we ll` (2 token), Whisper so'zida esa `well`
# (1 token) bo'lib qolardi va qisqartmali gaplar hech qachon topilmasdi.
_APOSTROPHES = re.compile(r"['’ʼ`]")


def _norm_tokens(text: str) -> list[str]:
    """Matnni solishtirish uchun sodda tokenlarga bo'ladi (harf+raqamgina)."""
    cleaned = _APOSTROPHES.sub("", (text or "").lower())
    return [t for t in re.sub(r"[^a-z0-9]+", " ", cleaned).split() if t]


def _word_tokens(words: list[dict]) -> list[str]:
    """Whisper so'zlari — `_norm_tokens` bilan AYNAN bir xil qoidada."""
    out: list[str] = []
    for w in words:
        cleaned = _APOSTROPHES.sub("", (w.get("word") or "").lower())
        out.append(re.sub(r"[^a-z0-9]+", "", cleaned))
    return out


def _locate_phrase(word_toks: list[str], words: list[dict], phrase: str) -> float | None:
    """Iqtibos `words_json` da qayerda boshlanishini topadi (soniyada).

    Avval to'liq moslik, topilmasa dastlabki `_MIN_MATCH` so'z bo'yicha.
    Topilmasa `None` — u holda AI bergan vaqt o'zgarishsiz qoladi.
    """
    target = _norm_tokens(phrase)
    if len(target) < _MIN_MATCH:
        return None
    for probe in (target, target[:_MIN_MATCH]):
        n = len(probe)
        for i in range(len(word_toks) - n + 1):
            if word_toks[i:i + n] == probe:
                return float(words[i].get("start") or 0)
    return None


def align_proof_timestamps(quiz: dict, words: list[dict]) -> int:
    """`proof_from_text` dagi `[t]` belgilarini HAQIQIY vaqt bilan almashtiradi.

    ## Nima uchun kerak

    Claude iqtibosning har bo'lagiga to'g'ri timestamp qo'ymaydi: ko'pincha
    iqtibos BOSHLANGAN qatorning vaqtini keyingi bo'laklarga ham nusxalaydi.
    Natijada xato **doim bir tomonga** — belgilangan vaqt haqiqiydan ERTAROQ.
    Amalda o'lchandi: +2.2 s dan +10.7 s gacha. Foydalanuvchi "savol joyi"
    indikatorida 33-soniyani ko'rib, javobni 47-soniyada eshitardi.

    Bizda haqiqat manbai bor — `words_json` (Whisper so'z-darajasidagi
    timestamp). Shu bois **AI ning vaqtiga ishonmaymiz**: har bo'lakni so'zlar
    oqimidan topib, vaqtni qayta hisoblaymiz. Topilmasa (Claude parafraz
    qilgan bo'lsa) eski qiymat qoladi — yomonlashtirmaydi.

    Qaytaradi: nechta timestamp tuzatilgani.
    """
    if not words:
        return 0
    word_toks = _word_tokens(words)
    fixed = 0
    for key in ("multiple_choice_questions", "tfng_questions", "true_false_questions",
                "fill_gap_questions"):
        for q in quiz.get(key) or []:
            if not isinstance(q, dict):
                continue
            proof = q.get("proof_from_text") or ""
            if not proof:
                continue
            # `[t] matn [t] matn ...` — har bo'lakni alohida tuzatamiz.
            pieces = _PROOF_SPLIT_RE.split(proof)
            if len(pieces) < 3:
                continue
            head, rest = pieces[0], pieces[1:]
            out = [head]
            for i in range(0, len(rest) - 1, 2):
                claimed, frag = rest[i], rest[i + 1]
                real = _locate_phrase(word_toks, words, frag)
                if real is None:
                    out.append(f"[{claimed}]")
                else:
                    if abs(real - float(claimed)) > 0.05:
                        fixed += 1
                    out.append(f"[{real:.1f}]")
                out.append(frag)
            q["proof_from_text"] = "".join(out)
    return fixed


def _timestamped_text(words: list[dict], line_gap_ms: int = 350) -> str:
    """Whisper so'z timestamp'laridan Claude'ga yuboriladigan matn tuzadi.

    Har qator: `[<start_sec>] <gap yoki gap qismi>` — yangi qator quyidagi
    hollarda boshlanadi:
      - Oldingi so'zdan `line_gap_ms` dan uzoq tanaffus (default 350 ms)
      - Oldingi so'z gap tugash belgisi bilan tugagan (`.`, `!`, `?`, `…`)

    Bu Claude'ga har gap uchun ANIQ timestamp beradi — "isbot" tugmasi shu
    vaqtdan boshlab videoni oynadi, natijada aynan iqtibos qilingan gap
    boshidan eshitiladi.

    Format o'zgardi: qavs `[3.4] ...` — prompt Claude'dan xuddi shu formatda
    proof_from_text qaytarishni so'raydi.
    """
    if not words:
        return ""
    lines: list[str] = []
    line_words: list[str] = []
    line_start: float | None = None
    prev_end: float | None = None
    prev_tok = ""
    for w in words:
        s = float(w.get("start") or 0)
        e = float(w.get("end") or s)
        tok = (w.get("word") or "").strip()
        if not tok:
            continue
        prev_end_of_sentence = prev_tok.endswith(_SENTENCE_END) if prev_tok else False
        big_gap = prev_end is not None and (s - prev_end) * 1000 > line_gap_ms
        if line_words and (prev_end_of_sentence or big_gap):
            lines.append(f"[{line_start:.1f}] " + " ".join(line_words))
            line_words = []
            line_start = None
        if line_start is None:
            line_start = s
        line_words.append(tok)
        prev_end = e
        prev_tok = tok
    if line_words and line_start is not None:
        lines.append(f"[{line_start:.1f}] " + " ".join(line_words))
    return "\n".join(lines)


# Video davomiyligiga qarab savol soni: har 30 s uchun 1 tadan.
# `Short` (qisqa) uchun eski qoida: base 2+2 + har 30 s +1/+1 (fill_gap yo'q).
# `News/Cartoon/Movie` uchun: har 30 s → 1 MCQ + 1 TFNG + 1 FillGap. Har biri
# min 2, max 10.
def compute_question_counts(duration_sec: int, content_type: str = "short") -> tuple[int, int, int]:
    """Qaytaradi (MCQ, TFNG, FillGap) sonlarini."""
    d = int(duration_sec or 0)
    if content_type == "short":
        # Eski logika — fill_gap yo'q
        if d <= 0: return 2, 2, 0
        extra = max(0, (d - 60) // 30)
        n = min(8, 2 + extra)
        return n, n, 0
    # News / Cartoon / Movie
    per_30 = max(1, (d + 15) // 30)   # 30 s → 1, 90 s → 3, ...
    n = min(10, max(2, per_30))
    return n, n, n


# Whisper'dan oldin bailout: video juda qisqa bo'lsa tokenlarni behuda sarflamaymiz.
SHORTS_MIN_DURATION_SEC = 8            # kamida shuncha bo'lmasa Whisper chaqirilmaydi
SHORTS_MIN_WORDS = 20                  # transkript shundan qisqa bo'lsa Claude chaqirilmaydi


def _fetch_youtube_meta(url: str) -> dict:
    """yt-dlp meta ma'lumotini olish (title, duration_sec, id) — yuklamasdan.

    Xato bo'lsa bo'sh dict qaytaradi — chaqiruvchi fallback qo'yadi.
    """
    try:
        import yt_dlp
    except ImportError:
        return {}
    try:
        opts = {
            "quiet": True, "no_warnings": True,
            "skip_download": True, "noplaylist": True,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False) or {}
        return {
            "title": (info.get("title") or "").strip(),
            "duration": int(info.get("duration") or 0),
            "id": info.get("id") or "",
        }
    except Exception as exc:
        logger.warning("fetch_youtube_meta failed for %s: %s", url, exc)
        return {}


# --- AI: transkript va savollar ---------------------------------------------

def _run_whisper(audio_path) -> tuple[str, list[dict]]:
    """Whisper chaqiruvi — (full_text, words[]) qaytaradi. Xato → TranscriptionError."""
    client = _get_client()
    try:
        with open(audio_path, "rb") as f:
            result = client.audio.transcriptions.create(
                model="whisper-1", file=f, language="en",
                response_format="verbose_json",
                timestamp_granularities=["word", "segment"],
            )
    except Exception as exc:
        raise TranscriptionError(_friendly_openai_error(str(exc))) from exc

    full_text = (getattr(result, "text", "") or "").strip()
    words = [
        {"start": float(w.start), "end": float(w.end), "word": (w.word or "").strip()}
        for w in (result.words or [])
    ]
    return full_text, words


def _run_claude(timestamped_text: str, mcq_count: int, tfng_count: int,
                fillgap_count: int) -> dict:
    """Claude Haiku'ga prompt_shorts.txt yuborib JSON qaytaradi.

    `mcq_count`/`tfng_count` — video davomiyligiga qarab tanlangan savol soni,
    prompt ichidagi `{MCQ_COUNT}` / `{TFNG_COUNT}` placeholder'lariga qo'yiladi.

    `CLAUDE_API_KEY` .env dan olinadi. Xato bo'lsa TranscriptionError.
    """
    api_key = getattr(settings, "CLAUDE_API_KEY", "") or os.environ.get("CLAUDE_API_KEY", "")
    if not api_key:
        raise TranscriptionError(
            "CLAUDE_API_KEY topilmadi. backend/.env fayliga qo'shing (Anthropic kaliti)."
        )
    try:
        from anthropic import Anthropic
    except ImportError as exc:
        raise TranscriptionError(
            "anthropic paketi o'rnatilmagan. `pip install anthropic` qiling."
        ) from exc

    # Prompt shabloni — `ai/prompt_shorts.txt` dan o'qiladi. Fayl bir necha
    # joyda bo'lishi mumkin (dev repo ildizi / docker mount), shu bois robust
    # resolver ishlatamiz.
    try:
        system_prompt = _read_ai_prompt("prompt_shorts.txt")
    except OSError as exc:
        raise TranscriptionError(f"prompt_shorts.txt o'qib bo'lmadi: {exc}") from exc

    system_prompt = (system_prompt
                     .replace("{MCQ_COUNT}", str(mcq_count))
                     .replace("{TFNG_COUNT}", str(tfng_count))
                     .replace("{FILLGAP_COUNT}", str(fillgap_count)))

    # `max_tokens` savol soniga qarab o'sadi. Ilgari qat'iy 2048 edi — uzun
    # news/movie videolarida (10 MCQ + 10 TFNG + 10 fill = 30 savol) javob
    # o'rtasidan kesilib, JSON parse xatosi bilan tugardi. Prompt endi yuqori
    # darajalarda uzunroq (parafraz qilingan) savollar so'raydi, shu bois
    # zaxira yanada kerak. Haiku 4.5 ning chegarasi 64 000 — 16 000 xavfsiz
    # va HTTP timeout'ga ham tushmaydi (stream ishlatmayapmiz).
    total_q = mcq_count + tfng_count + fillgap_count
    max_tokens = min(16000, 2000 + 400 * max(1, total_q))

    client = Anthropic(api_key=api_key)
    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=max_tokens,
            temperature=0,
            system=[{
                "type": "text", "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": timestamped_text}],
        )
    except Exception as exc:
        msg = str(exc).lower()
        if "authentication" in msg or "invalid" in msg:
            friendly = "CLAUDE_API_KEY noto'g'ri yoki muddati o'tgan."
        elif "rate" in msg or "429" in msg:
            friendly = "Claude rate limit — biroz kutib qayta urining."
        elif "credit" in msg or "quota" in msg or "billing" in msg:
            friendly = "Claude kvota tugagan yoki hisob to'lanmagan."
        else:
            friendly = f"Claude xatosi: {str(exc)[:400]}"
        raise TranscriptionError(friendly) from exc

    # Javob chegaraga tirab kesilgan bo'lsa JSON baribir buzuq bo'ladi —
    # chalkash "parse xatosi" o'rniga aniq sabab beramiz.
    if getattr(response, "stop_reason", None) == "max_tokens":
        raise TranscriptionError(
            f"Claude javobi {max_tokens} token chegarasiga tirab kesildi "
            f"({total_q} ta savol so'ralgan edi). Savol sonini kamaytiring yoki "
            f"`_run_claude` dagi max_tokens formulasini oshiring."
        )

    raw = response.content[0].text.strip()
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TranscriptionError(
            f"Claude natijasini JSON qilib o'qib bo'lmadi: {exc}. Xom natija: {raw[:400]}"
        ) from exc


def _validate_ai_result(data: dict, mcq_count: int, tfng_count: int,
                        fillgap_count: int) -> None:
    """AI natijasini tekshirish — kutilgan struktura yo'q bo'lsa xato."""
    ev = (data or {}).get("evaluation") or {}
    quiz = (data or {}).get("quiz") or {}
    if not ev.get("cefr_from") or not ev.get("cefr_to"):
        raise TranscriptionError("AI natijasi noto'g'ri: cefr_from/cefr_to yo'q.")
    mcq = quiz.get("multiple_choice_questions") or []
    tfng = quiz.get("tfng_questions") or quiz.get("true_false_questions") or []
    fill = quiz.get("fill_gap_questions") or []
    # Tolerans: AI ±1 xato qilsa ham qabul qilamiz.
    if len(mcq) < 1 or abs(len(mcq) - mcq_count) > 1:
        raise TranscriptionError(
            f"AI {mcq_count} ta MCQ o'rniga {len(mcq)} ta savol qaytardi."
        )
    if len(tfng) < 1 or abs(len(tfng) - tfng_count) > 1:
        raise TranscriptionError(
            f"AI {tfng_count} ta TFNG o'rniga {len(tfng)} ta savol qaytardi."
        )
    if fillgap_count > 0 and (len(fill) < 1 or abs(len(fill) - fillgap_count) > 1):
        raise TranscriptionError(
            f"AI {fillgap_count} ta FillGap o'rniga {len(fill)} ta savol qaytardi."
        )
    allowed = {"true", "false", "not given"}
    for q in tfng:
        ans = (q.get("answer") or "").strip().lower()
        if ans not in allowed:
            raise TranscriptionError(f"TFNG javob noto'g'ri: {q.get('answer')!r}")
    for q in fill:
        s = (q.get("sentence") or "")
        # AI ba'zan `answer` (string), ba'zan `answers` (array) qaytaradi.
        # Ikkalasini ham qabul qilamiz va massivga aylantirib saqlaymiz.
        answers = q.get("answers")
        if not answers:
            single = (q.get("answer") or "").strip()
            answers = [single] if single else []
        if isinstance(answers, str):
            answers = [answers]
        answers = [str(x).strip() for x in answers if str(x).strip()]
        if "___" not in s or not answers:
            raise TranscriptionError(
                "FillGap noto'g'ri (sentence ichida `___` bo'lishi va answers bo'sh bo'lmasligi kerak)."
            )
        # Normal saqlash uchun schema'ni bir xil qilib qaytaramiz
        q["answers"] = answers
        q.setdefault("hint", "")


# --- Asosiy entry point -----------------------------------------------------

def generate_short(short: Short) -> Short:
    """Bitta Short'ni to'liq yaratadi: audio → transkript → savollar."""
    if not short.youtube_link:
        raise TranscriptionError("YouTube havolasi berilmagan.")

    short.transcription_status = Short.TranscriptionStatus.PROCESSING
    short.transcription_error = ""
    short.save(update_fields=["transcription_status", "transcription_error", "updated_at"])

    # 1. Meta ma'lumot — title va duration (yuklamasdan)
    meta = _fetch_youtube_meta(short.youtube_link)
    yt_id = mock_data.extract_youtube_id(short.youtube_link) or meta.get("id") or ""
    if yt_id:
        short.youtube_id = yt_id
    if not short.title:
        # yt-dlp meta VPS'da (data-markaz IP) ko'pincha sarlavhani BERMAYDI
        # ("No title found in player responses") → bo'sh title. oEmbed esa yengil
        # va bloklanmaydi (diktantlar shundan oladi) — fallback qilamiz.
        title = (meta.get("title") or "").strip() or fetch_youtube_title(short.youtube_link)
        if title:
            short.title = title[:250]
    if meta.get("duration") and not short.duration_sec:
        short.duration_sec = int(meta["duration"])

    # 1b. Erta bailout — yt-dlp meta orqali. Juda qisqa video bo'lsa
    # (masalan 4 s) audio yuklab olishga ham hojat yo'q — resursni saqlaymiz.
    meta_dur = int(meta.get("duration") or 0)
    if meta_dur and meta_dur < SHORTS_MIN_DURATION_SEC:
        msg = (f"Video juda qisqa: {meta_dur}s "
               f"(kamida {SHORTS_MIN_DURATION_SEC}s bo'lishi kerak).")
        _mark_failed(short, msg)
        raise TranscriptionError(msg)
    if meta_dur and meta_dur > MAX_DURATION_SEC:
        msg = (f"Video juda uzun: {meta_dur // 60}:{meta_dur % 60:02d} "
               f"(cheklov {MAX_DURATION_SEC // 60} daqiqa).")
        _mark_failed(short, msg)
        raise TranscriptionError(msg)

    # 2. Audio yuklab olish + Whisper transkript
    try:
        with _download_youtube_audio(short.youtube_link) as audio_path:
            size_mb = audio_path.stat().st_size / (1024 * 1024)
            if size_mb > MAX_FILE_SIZE_MB:
                raise TranscriptionError(
                    f"Audio juda katta: {size_mb:.1f} MB (cheklov {MAX_FILE_SIZE_MB} MB).",
                )
            duration = measure_audio_duration(audio_path)
            if duration < SHORTS_MIN_DURATION_SEC:
                raise TranscriptionError(
                    f"Video juda qisqa: {duration}s "
                    f"(kamida {SHORTS_MIN_DURATION_SEC}s bo'lishi kerak).",
                )
            if duration > MAX_DURATION_SEC:
                raise TranscriptionError(
                    f"Video juda uzun: {duration // 60}:{duration % 60:02d} "
                    f"(cheklov {MAX_DURATION_SEC // 60} daqiqa).",
                )
            short.duration_sec = short.duration_sec or duration
            full_text, words = _run_whisper(audio_path)
    except TranscriptionError as exc:
        _mark_failed(short, str(exc))
        raise
    except Exception as exc:
        _mark_failed(short, f"Kutilmagan xato: {exc}")
        raise TranscriptionError(f"Kutilmagan xato: {exc}") from exc

    if not full_text:
        _mark_failed(short, "Whisper bo'sh matn qaytardi.")
        raise TranscriptionError("Whisper bo'sh matn qaytardi.")

    # 2b. Transkript juda qisqa bo'lsa Claude'ga bermaymiz — token behuda ketmasin.
    word_count = len([w for w in full_text.split() if w])
    if word_count < SHORTS_MIN_WORDS:
        short.full_text = full_text
        short.words_json = words
        msg = (f"Transkript juda qisqa: {word_count} so'z "
               f"(savollar uchun kamida {SHORTS_MIN_WORDS} kerak).")
        _mark_failed(short, msg)
        short.save(update_fields=[
            "full_text", "words_json", "updated_at",
        ])
        raise TranscriptionError(msg)

    short.full_text = full_text
    short.words_json = words

    # 3. Claude Haiku — savollar. Video davomiyligi + kontent turiga qarab
    # dinamik son: short → 2+2+0, boshqalar → per_30 * (1 MCQ + 1 TFNG + 1 FillGap).
    mcq_count, tfng_count, fillgap_count = compute_question_counts(
        short.duration_sec, short.content_type,
    )
    timestamped = _timestamped_text(words) or full_text
    try:
        ai = _run_claude(timestamped, mcq_count, tfng_count, fillgap_count)
        _validate_ai_result(ai, mcq_count, tfng_count, fillgap_count)
    except TranscriptionError as exc:
        _mark_failed(short, str(exc))
        # transkript baribir saqlansin — foydali
        short.save(update_fields=[
            "youtube_id", "title", "duration_sec", "full_text", "words_json", "updated_at",
        ])
        raise

    ev = ai["evaluation"]
    # 1) AI bergan `[t]` larni HAQIQIY vaqt bilan almashtiramiz (`words_json`
    #    haqiqat manbai). 2) Keyin isbot vaqti bo'yicha saralaymiz — tartib
    #    tuzatilgan vaqtga tayanishi kerak, aks holda saralash ham xato bo'ladi.
    quiz = ai["quiz"]
    align_proof_timestamps(quiz, words)
    if not sequence_is_chronological(quiz):
        # Raqamlash baribir to'g'rilanadi (`_number_globally`), lekin
        # promptning 5-qoidasi buzilgani bilinib tursin — prompt qanchalik
        # ishlayotganini shu log bilan o'lchaymiz.
        logger.warning(
            "Short #%s: AI savollarni xronologik bermadi (prompt 5-qoida).",
            short.pk,
        )
    quiz = _order_quiz(quiz)
    short.cefr_from = (ev.get("cefr_from") or "").strip()
    short.cefr_to = (ev.get("cefr_to") or "").strip()
    short.tags = list(ev.get("tags") or [])[:10]
    short.mcq_questions = quiz.get("multiple_choice_questions") or []
    short.tfng_questions = (
        quiz.get("tfng_questions") or quiz.get("true_false_questions") or []
    )
    short.fill_gap_questions = quiz.get("fill_gap_questions") or []
    short.transcription_status = Short.TranscriptionStatus.DONE
    short.transcription_error = ""
    short.is_published = True
    short.save()
    return short


def generate_dictation_tests(dictation) -> None:
    """Diktantning Whisper natijasi (full_text/words_json) asosida Claude
    orqali listening test savollarini yaratadi va modelga saqlaydi.

    - Duration bo'yicha `compute_question_counts(duration, "news")` — har
      30 s uchun 1 MCQ + 1 TFNG + 1 FillGap (news/movie/cartoon prompti).
    - Transkript bo'sh yoki juda qisqa bo'lsa xato bilan tugaydi.
    """
    if not dictation.full_text or len(dictation.full_text.split()) < SHORTS_MIN_WORDS:
        dictation.tests_status = "failed"
        dictation.tests_error = (
            f"Transkript juda qisqa yoki bo'sh (kamida {SHORTS_MIN_WORDS} so'z)."
        )
        dictation.save(update_fields=["tests_status", "tests_error", "updated_at"])
        raise TranscriptionError(dictation.tests_error)

    dictation.tests_status = "processing"
    dictation.tests_error = ""
    dictation.save(update_fields=["tests_status", "tests_error", "updated_at"])

    mcq_n, tfng_n, fill_n = compute_question_counts(dictation.duration_sec, "news")
    timestamped = _timestamped_text(dictation.words_json or []) or dictation.full_text
    try:
        ai = _run_claude(timestamped, mcq_n, tfng_n, fill_n)
        _validate_ai_result(ai, mcq_n, tfng_n, fill_n)
    except TranscriptionError as exc:
        dictation.tests_status = "failed"
        dictation.tests_error = str(exc)[:2000]
        dictation.save(update_fields=["tests_status", "tests_error", "updated_at"])
        raise

    # Avval `[t]` larni haqiqiy vaqtga moslaymiz, keyin saralab raqamlaymiz
    # (`align_proof_timestamps` va `_order_quiz` izohlariga qarang).
    quiz = ai["quiz"]
    align_proof_timestamps(quiz, dictation.words_json or [])
    if not sequence_is_chronological(quiz):
        logger.warning(
            "Dictation #%s: AI savollarni xronologik bermadi (prompt 5-qoida).",
            dictation.pk,
        )
    quiz = _order_quiz(quiz)
    dictation.mcq_questions = quiz.get("multiple_choice_questions") or []
    dictation.tfng_questions = (
        quiz.get("tfng_questions") or quiz.get("true_false_questions") or []
    )
    dictation.fill_gap_questions = quiz.get("fill_gap_questions") or []
    dictation.tests_status = "done"
    dictation.tests_error = ""
    dictation.save(update_fields=[
        "mcq_questions", "tfng_questions", "fill_gap_questions",
        "tests_status", "tests_error", "updated_at",
    ])


def _mark_failed(short: Short, message: str) -> None:
    try:
        short.transcription_status = Short.TranscriptionStatus.FAILED
        short.transcription_error = (message or "")[:2000]
        short.save(update_fields=[
            "transcription_status", "transcription_error", "updated_at",
        ])
    except Exception as exc:
        logger.exception("Short mark_failed failed: %s", exc)

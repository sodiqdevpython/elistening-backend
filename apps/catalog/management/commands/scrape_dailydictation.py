"""dailydictation.com darslarini `Dictation` ga import qiladi.

    python manage.py scrape_dailydictation 1          # har bo'limdan 1 ta YANGI dars
    python manage.py scrape_dailydictation all        # qolgan hammasi
    python manage.py scrape_dailydictation 0          # faqat daraja xaritasi (LEVELS.md)
    python manage.py scrape_dailydictation 2 --per-level --types news,ted --levels B1,B2

Serverda:  docker compose exec web python manage.py scrape_dailydictation all

* **Bazada bori qayta olinmaydi.** Har dars `source_url` bilan saqlanadi;
  ishga tushganda bazadagi ID'lar yig'iladi va ular umuman so'ralmaydi.
  COUNT — har bo'limdan nechta YANGI dars (qayta ishga tushirsangiz keyingilari
  keladi). Mavjudlarini yangilash kerak bo'lsa — `--update`.
* **Har dars tayyor bo'lishi bilan bazaga yoziladi** — to'xtab qolsa (Ctrl+C,
  tarmoq) qilingan ish yo'qolmaydi, qayta ishga tushirish qolganidan davom etadi.
* **Ko'rinishi** "Sayt sozlamalari" → "Tashqi manbadan olingan darslar
  ko'rinsin" belgisiga bog'liq (`--hidden` — baribir yashirin import).
* **Token sarflanmaydi:** `post_save` audio/YouTube'li diktantga Whisper
  (yoki Haiku) qo'yadi — import paytida o'chiriladi va keyin AIJob tekshiriladi.

Medical (OET) va IPA bo'limlari ataylab yo'q — bizda bu mavzular olib tashlangan.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils.text import slugify

BASE = "https://dailydictation.com"
MEDIA_SUBDIR = "dictations/audio/dailydictation"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; listening-uz-import/1.0)"}

# Ularning bo'limi → bizning `Dictation.type`.
CATEGORIES = {
    "short-stories": "short_story",
    "english-conversations": "conversation",
    "stories-for-kids": "kids_story",
    "toeic": "toeic",
    "ielts-listening": "ielts",
    "youtube": "random_video",
    "news": "news",
    "ted-ed": "ted",
    "toefl-listening": "toefl",
    "numbers": "number",
    "spelling-names": "spelling",
}
CEFR = ("A1", "A2", "B1", "B2", "C1", "C2")

LESSON_RE = re.compile(
    r'<a class="text-decoration-none" href="(/exercises/(?P<cat>[\w-]+)/(?P<slug>[\w-]+)\.(?P<id>\d+)/listen-and-type)">'
    r'\s*<span class="fw-semibold">(?P<title>.*?)</span>', re.S)
GROUP_RE = re.compile(r'data-bs-target="#lessonGroup\d+"[^>]*>(.*?)</button>', re.S)
GLOBALS_RE = re.compile(r"window\.appGlobals\s*=\s*(\{.*?\});\s*</script>", re.S)


# ── Sof yordamchilar (testlanadi) ─────────────────────────────────────────
def clean(s: str | None) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s or "")).split())


def parse_category(cat: str, page: str) -> list[dict]:
    """Bo'lim sahifasidagi har dars: daraja ("Vocab level"), PRO, guruh.

    Darslar sahifada ketma-ket bloklar — har blok o'z havolasidan keyingi
    havolagacha. Guruh (Section 1, Cam 21, Level B1 (2) ...) — blokdan
    oldingi eng yaqin accordion sarlavhasi."""
    groups = [(m.start(), re.sub(r"\(\d+\s*lessons?\)", "", clean(m.group(1))).strip())
              for m in GROUP_RE.finditer(page)]
    matches = [m for m in LESSON_RE.finditer(page) if m.group("cat") == cat]
    lessons, seen = [], set()
    for i, m in enumerate(matches):
        lid = int(m.group("id"))
        if lid in seen:
            continue
        seen.add(lid)
        end = matches[i + 1].start() if i + 1 < len(matches) else len(page)
        block = page[m.end():end]
        level = re.search(r"Vocab level:\s*([ABC][12])", block)
        parts = re.search(r"(\d+)\s*parts", block)
        group = ""
        for pos, name in groups:
            if pos < m.start():
                group = name
        lessons.append({
            "id": lid,
            "category": cat,
            "type": CATEGORIES[cat],
            "slug": m.group("slug"),
            "title": clean(m.group("title")),
            "level": level.group(1) if level else "",
            "parts": int(parts.group(1)) if parts else None,
            "pro": 'title="PRO lesson"' in block,
            "group": group,
            "url": BASE + m.group(1),
        })
    return lessons


def select(catalog: dict[str, list[dict]], count: int | None, known: set[int], *,
           levels: set[str] = frozenset(), per_level: bool = False, skip_pro: bool = False) -> list[dict]:
    """Bazada YO'Q darslardan har bo'limga `count` tadan (None — hammasi)."""
    picked = []
    for items in catalog.values():
        pool = [x for x in items
                if x["id"] not in known
                and (not levels or x["level"] in levels)
                and not (skip_pro and x["pro"])]
        if count is None:
            picked += pool
        elif per_level:
            for level in dict.fromkeys(x["level"] for x in pool):
                picked += [x for x in pool if x["level"] == level][:count]
        else:
            picked += pool[:count]
    return picked


def slim_lesson(meta: dict, g: dict) -> dict:
    """appGlobals'dan faqat kerakli qism (+ katalogdagi daraja va guruh)."""
    return {
        "source": meta,
        "lessonName": g.get("lessonName"),
        "audioSrc": g.get("audioSrc") or "",
        "youtubeVideoId": g.get("youtubeVideoId") or "",
        "challenges": [
            {
                "position": c.get("position"),
                "content": c.get("content"),
                "timeStart": c.get("timeStart"),
                "timeEnd": c.get("timeEnd"),
                "hints": c.get("hints") or ([c["hint"]] if c.get("hint") else []),
                # Muqobil yozuvlar: [["3", "three"], ["I'm", "I am"], ...]
                "solution": c.get("solution"),
                "explanation": c.get("explanation"),
            }
            for c in g.get("challenges") or []
        ],
    }


def build_body(challenges: list[dict]) -> list[dict]:
    """Har challenge = bitta chunk. `fixed` — frontend uni gapga birlashtirib
    yubormasin ("Mary", "(797) 285-6959" nuqtasiz, lekin alohida mashq)."""
    body = []
    for c in sorted(challenges, key=lambda c: c.get("position") or 0):
        text = " ".join((c.get("content") or "").split())
        if not text:
            continue
        start = int(round(float(c.get("timeStart") or 0) * 1000))
        end = int(round(float(c.get("timeEnd") or 0) * 1000))
        chunk = {"start_ms": start, "end_ms": max(end, start + 1), "text": text, "fixed": True}
        hints = [h for h in (clean(x) for x in c.get("hints") or []) if h]
        if hints:
            chunk["hints"] = hints
        body.append(chunk)
    return body


def lesson_file(meta: dict) -> str:
    return f"{meta['id']}-{meta['slug']}"


def audio_seconds(path: Path, body: list[dict]) -> int:
    """`Dictation.save()` ham o'lchaydi, lekin `if info and info.info` — ID3
    tegi yo'q mp3'da mutagen obyekti falsy bo'lib, 0 qolib ketadi."""
    try:
        from mutagen import File as MutagenFile
        info = MutagenFile(str(path))
        if info is not None and info.info and info.info.length:
            return int(round(info.info.length))
    except Exception:
        pass
    return (body[-1]["end_ms"] + 500) // 1000


def save_lesson(rec: dict, audio_rel: str, publish: bool) -> str:
    """Bitta darsni bazaga yozadi → `new` | `updated` | `empty`.

    Mavjud darsda `is_published` ga TEGILMAYDI — admin uni qo'lda yashirgan
    bo'lishi mumkin (`--update` bilan qayta olinganda ham).
    """
    from apps.catalog.models import Dictation

    body = build_body(rec["challenges"])
    if not body:
        return "empty"
    meta = rec["source"]
    d = (Dictation.objects.filter(source_url=meta["url"]).first()
         or Dictation.objects.filter(slug__startswith=f"dd-{meta['id']}-").first())
    created = d is None
    if created:
        d = Dictation(slug=slugify(f"dd-{meta['id']}-{meta['slug']}")[:280], is_published=publish)
    d.source_url = meta["url"]
    d.title = (rec.get("lessonName") or meta["title"])[:250]
    d.type = meta["type"]
    d.cefr_level = meta["level"]
    d.body = body
    d.full_text = " ".join(c["text"] for c in body)
    d.words_json = []
    # `done` — admin saqlasa Whisper body'ni ustidan yozib yubormasin.
    d.transcription_status = Dictation.TranscriptionStatus.DONE
    d.transcription_error = ""
    yt = rec.get("youtubeVideoId") or ""
    d.is_media = bool(yt)
    d.youtube_link = f"https://www.youtube.com/watch?v={yt}" if yt else ""
    if audio_rel:
        d.audio.name = audio_rel
        d.audio_duration_sec = audio_seconds(Path(settings.MEDIA_ROOT) / audio_rel, body)
    else:
        d.audio = None
        d.audio_duration_sec = (body[-1]["end_ms"] + 500) // 1000
    d.save()
    return "new" if created else "updated"


@contextmanager
def ai_signals_off():
    """`post_save` audio/YouTube'li diktantga Whisper (status `done` bo'lsa
    Haiku) qo'yadi — dev'da worker thread uni darrov oladi, prod'da Celery.
    Matn manbaning o'zidan keladi, token kerak emas."""
    from django.db.models.signals import post_save
    from apps.catalog import signals as sig
    from apps.catalog.models import DICTATION_PROXIES, Dictation

    post_save.disconnect(sig.enqueue_dictation_ai, sender=Dictation)
    for p in DICTATION_PROXIES:
        post_save.disconnect(sender=p, dispatch_uid=f"enqueue_dictation_ai_{p.__name__}")
    try:
        yield
    finally:
        post_save.connect(sig.enqueue_dictation_ai, sender=Dictation)
        for p in DICTATION_PROXIES:
            post_save.connect(sig.enqueue_dictation_ai, sender=p,
                              dispatch_uid=f"enqueue_dictation_ai_{p.__name__}")


def write_text(path: Path, text: str) -> None:
    """Avval `.tmp` ga, keyin almashtirish — yarim yozilgan fayl qolmasin."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def levels_markdown(catalog: dict[str, list[dict]]) -> str:
    levels = list(CEFR) + [""]
    lines = [
        "# dailydictation.com — darajalar xaritasi",
        "",
        f"Yangilangan: {time.strftime('%Y-%m-%d %H:%M')}. 💎 = saytda PRO belgili dars.",
        "",
        "| Bo'lim → bizda | " + " | ".join(l or "darajasiz" for l in levels) + " | Jami | PRO |",
        "|---|" + "---:|" * (len(levels) + 2),
    ]
    for cat, items in catalog.items():
        counts = [sum(1 for x in items if x["level"] == l) for l in levels]
        lines.append(f"| {cat} → `{CATEGORIES[cat]}` | " + " | ".join(str(c or "") for c in counts)
                     + f" | {len(items)} | {sum(x['pro'] for x in items)} |")
    for cat, items in catalog.items():
        lines += ["", f"## {cat} → `{CATEGORIES[cat]}`"]
        for l in levels:
            group = [x for x in items if x["level"] == l]
            if not group:
                continue
            lines += ["", f"### {l or 'Darajasiz'} ({len(group)})", ""]
            lines += [f"- {'💎 ' if x['pro'] else ''}{x['title']} — `{x['id']}`"
                      + (f" · {x['group']}" if x["group"] else "") for x in group]
    return "\n".join(lines) + "\n"


def fmt_eta(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60:02d}:{rest % 60:02d}"


# ── HTTP ──────────────────────────────────────────────────────────────────
async def fetch(client: httpx.AsyncClient, url: str) -> httpx.Response:
    """429/5xx va tarmoq xatolarida 4 martagacha qayta urinadi."""
    for attempt in range(4):
        try:
            r = await client.get(url)
            if r.status_code == 429 or r.status_code >= 500:
                raise httpx.HTTPStatusError(f"{r.status_code}", request=r.request, response=r)
            r.raise_for_status()
            return r
        except (httpx.TransportError, httpx.HTTPStatusError) as e:
            retryable = not isinstance(e, httpx.HTTPStatusError) or (
                e.response.status_code == 429 or e.response.status_code >= 500)
            if attempt == 3 or not retryable:
                raise
            await asyncio.sleep(1.5 * 2 ** attempt)
    raise AssertionError("unreachable")


async def download(client: httpx.AsyncClient, url: str, dst: Path) -> int:
    if dst.exists() and dst.stat().st_size > 0:
        return dst.stat().st_size
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".part")
    for attempt in range(4):
        try:
            async with client.stream("GET", url) as r:
                r.raise_for_status()
                with open(tmp, "wb") as f:
                    async for part in r.aiter_bytes(1 << 16):
                        f.write(part)
            os.replace(tmp, dst)
            return dst.stat().st_size
        except (httpx.TransportError, httpx.HTTPStatusError):
            if attempt == 3:
                tmp.unlink(missing_ok=True)
                raise
            await asyncio.sleep(1.5 * 2 ** attempt)
    raise AssertionError("unreachable")


# ── Buyruq ────────────────────────────────────────────────────────────────
class Command(BaseCommand):
    help = "dailydictation.com darslarini import qiladi (bazada borini qayta olmaydi)."

    def add_arguments(self, parser):
        parser.add_argument("count", nargs="?", default="1",
                            help="Har bo'limdan nechta YANGI dars: son yoki 'all'. 0 — faqat LEVELS.md")
        parser.add_argument("--types", default="", help="Vergul bilan: short_story,news yoki short-stories")
        parser.add_argument("--levels", default="", help="Vergul bilan: A1,B2")
        parser.add_argument("--per-level", action="store_true", help="COUNT har DARAJA uchun")
        parser.add_argument("--skip-pro", action="store_true", help="Saytda 💎 PRO belgili darslarni olmaslik")
        parser.add_argument("--concurrency", type=int, default=4, help="Bir vaqtdagi so'rovlar (default 4)")
        parser.add_argument("--update", action="store_true",
                            help="Bazada BORLARINI ham qayta olib yangilash (odatda o'tkazib yuboriladi)")
        parser.add_argument("--hidden", action="store_true", help="Yangi darslarni yashirin import qilish")
        parser.add_argument("--out", default="", help="LEVELS.md, catalog.json va xom JSON papkasi")

    def handle(self, *args, **opts):
        from apps.catalog.imported import imported_source_ids
        from apps.catalog.models import AIJob, Dictation
        from apps.common.models import SiteConfig

        logging.getLogger("httpx").setLevel(logging.WARNING)
        try:  # Windows konsoli (cp1252) `‘` kabi belgilarda yiqilmasin
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
        raw = str(opts["count"]).strip().lower()
        if raw in ("*", "all"):
            count = None
        elif raw.isdigit():
            count = int(raw)
        else:
            raise CommandError("COUNT — son yoki 'all'")
        types = {x.strip() for x in opts["types"].split(",") if x.strip()}
        unknown = types - set(CATEGORIES) - set(CATEGORIES.values())
        if unknown:
            raise CommandError(f"Noma'lum mavzu: {', '.join(sorted(unknown))}. "
                               f"Bor: {', '.join(CATEGORIES.values())}")
        self.opts = opts
        self.types = types
        self.levels = {x.strip().upper() for x in opts["levels"].split(",") if x.strip()}
        self.out = Path(opts["out"] or settings.BASE_DIR / ".scrape" / "dailydictation")
        self.known = set() if opts["update"] else imported_source_ids()
        self.publish = not opts["hidden"] and SiteConfig.get_solo().show_imported

        t0 = time.time()
        jobs_before = set(AIJob.objects.values_list("pk", flat=True))
        with ai_signals_off():
            stats = asyncio.run(self.scrape(count))

        leaked = AIJob.objects.exclude(pk__in=jobs_before).filter(
            object_id__in=Dictation.objects.exclude(source_url="").values("pk"))
        if leaked.exists():  # bo'lmasligi kerak — lekin token ketmasin
            self.stdout.write(self.style.WARNING(f"{leaked.count()} ta AIJob paydo bo'ldi — o'chirildi"))
            leaked.delete()

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"Tayyor ({fmt_eta(time.time() - t0)}): {stats['new']} ta yangi, "
            f"{stats['updated']} ta yangilandi, {len(stats['failed'])} ta xato, "
            f"bazada oldindan bor: {stats['known']} ta."))
        if count != 0:
            state = "KO'RINADI" if self.publish else "YASHIRIN"
            self.stdout.write(f"Yangi darslar saytda: {state} (Sayt sozlamalari → "
                              "\"Tashqi manbadan olingan darslar ko'rinsin\").")
        for meta, error in stats["failed"][:20]:
            self.stdout.write(self.style.ERROR(f"  xato: {meta['type']} {meta['title']} — {error}"))
        if stats["failed"]:
            self.stdout.write("Qayta ishga tushirsangiz faqat qolganlari (xatolar ham) olinadi.")

    # ── Asinxron qism ────────────────────────────────────────────────────
    async def scrape(self, count):
        opts = self.opts
        limits = httpx.Limits(max_connections=opts["concurrency"],
                              max_keepalive_connections=opts["concurrency"])
        async with httpx.AsyncClient(headers=HEADERS, timeout=httpx.Timeout(60, connect=20),
                                     follow_redirects=True, limits=limits) as client:
            # Xarita uchun HAMMA bo'lim o'qiladi (11 so'rov) — `--types` faqat
            # qaysi darslar yuklanishini cheklaydi, LEVELS.md esa doim to'liq.
            self.stdout.write(f"Bo'limlar o'qilmoqda ({len(CATEGORIES)} ta)...")
            pages = await asyncio.gather(*(fetch(client, f"{BASE}/exercises/{c}") for c in CATEGORIES))
            catalog = {c: parse_category(c, r.text) for c, r in zip(CATEGORIES, pages)}
            write_text(self.out / "catalog.json", json.dumps(catalog, ensure_ascii=False, indent=1))
            write_text(self.out / "LEVELS.md", levels_markdown(catalog))
            self.print_levels(catalog)

            wanted = {c: v for c, v in catalog.items()
                      if not self.types or c in self.types or CATEGORIES[c] in self.types}
            in_scope = {x["id"] for items in wanted.values() for x in items}
            known = len(in_scope & self.known)
            picked = select(wanted, count, self.known, levels=self.levels,
                            per_level=opts["per_level"], skip_pro=opts["skip_pro"])
            stats = {"new": 0, "updated": 0, "failed": [], "known": known}
            self.stdout.write(f"\nBazada allaqachon bor: {known} ta — ular o'tkazib yuboriladi.")
            if not picked:
                self.stdout.write("Yangi dars yo'q." if count != 0 else "")
                return stats

            self.stdout.write(f"Yuklanadi: {len(picked)} ta dars (bir vaqtda {opts['concurrency']} ta)\n")
            self.progress = {"done": 0, "total": len(picked), "t0": time.time()}
            sem = asyncio.Semaphore(opts["concurrency"])
            save = sync_to_async(save_lesson, thread_sensitive=True)
            await asyncio.gather(*(self.one(client, sem, save, meta, stats) for meta in picked))
            return stats

    async def one(self, client, sem, save, meta, stats):
        t = meta["type"]
        detail = ""
        async with sem:
            try:
                page = (await fetch(client, meta["url"])).text
                m = GLOBALS_RE.search(page)
                if not m:
                    raise ValueError("window.appGlobals topilmadi")
                rec = slim_lesson(meta, json.loads(m.group(1)))
                write_text(self.out / "lessons" / t / f"{lesson_file(meta)}.json",
                           json.dumps(rec, ensure_ascii=False, indent=1))
                audio_rel = ""
                if rec["audioSrc"]:
                    ext = os.path.splitext(rec["audioSrc"].split("?")[0])[1] or ".mp3"
                    audio_rel = f"{MEDIA_SUBDIR}/{t}/{lesson_file(meta)}{ext}"
                    size = await download(client, rec["audioSrc"], Path(settings.MEDIA_ROOT) / audio_rel)
                    detail = f"audio {size / 1e6:.1f} MB"
                else:
                    detail = f"YouTube {rec['youtubeVideoId']}"
                result = await save(rec, audio_rel, self.publish)
                if result in ("new", "updated"):
                    stats[result] += 1
                hints = sum(1 for c in rec["challenges"] if c["hints"])
                status = {"new": "+", "updated": "~", "empty": "∅"}[result]
                detail = f"{len(rec['challenges'])} gap, {hints} hint, {detail}"
            except Exception as e:  # bitta dars butun ishni to'xtatmasin
                stats["failed"].append((meta, repr(e)[:200]))
                status, detail = "x", f"XATO: {e!r}"[:120]
        self.report(status, meta, detail)

    def report(self, status: str, meta: dict, detail: str) -> None:
        p = self.progress
        p["done"] += 1
        done, total = p["done"], p["total"]
        elapsed = time.time() - p["t0"]
        eta = fmt_eta(elapsed / done * (total - done))
        width = len(str(total))
        line = (f"[{done:>{width}}/{total} {done / total * 100:5.1f}% | qoldi ~{eta}] {status} "
                f"{meta['type']:12} {meta['level'] or '--'} {meta['title'][:44]:44} {detail}")
        style = self.style.ERROR if status == "x" else (lambda s: s)
        self.stdout.write(style(line))

    def print_levels(self, catalog: dict[str, list[dict]]) -> None:
        self.stdout.write(f"Daraja xaritasi: {self.out / 'LEVELS.md'}")
        header = " ".join(f"{l:>4}" for l in CEFR)
        self.stdout.write(f"  {'bo‘lim':24} {header}  {'—':>4} {'jami':>5}  PRO")
        for cat, items in catalog.items():
            counts = " ".join(f"{sum(1 for x in items if x['level'] == l) or '':>4}" for l in CEFR)
            none = sum(1 for x in items if not x["level"]) or ""
            pro = sum(x["pro"] for x in items)
            self.stdout.write(f"  {cat:24} {counts}  {none:>4} {len(items):>5}  {pro:>3}")

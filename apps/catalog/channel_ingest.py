"""Kanaldan video yig'ish oqimi.

Admin `ChannelIngestTask` yaratadi (URL + N + target). Signal AIJob'ga
(`kind=CHANNEL`, `step=INGEST`) yozadi va worker shu modulni chaqiradi.

Bu modul faqat `yt-dlp` ni chaqiradi va bazaga yozadi — AI transkript
va savol generatsiyasi keyingi bosqichda o'sha yaratilgan Short/Dictation
uchun signal orqali avtomatik ishga tushadi.
"""
from __future__ import annotations

import logging
import re

from django.utils import timezone

from .models import ChannelIngestTask, Dictation, Short, is_shorts_url


# Foydalanuvchi tanlagan bo'lim → qaysi modelga va qanday qiymatlar bilan
# yozish. 5 tadan bittasi — boshqasi qabul qilinmaydi.
#
#   SHORTS/MOVIES/CARTOONS/NEWS → Short(content_type=...)
#   RANDOM_VIDEOS               → Dictation(type=random_video, is_media=True)
# Bo'lim → (model, subtype).
#
# **Bo'lim turni AYTIB TURADI.** "Filmlar" ni tanlagan odam uzun YouTube
# videosi qo'shayotgani aniq — Shorts esa faqat `youtube.com/shorts/...` da
# bo'ladi va boshqa hech qayerda yo'q. Shu bois Film / Multfilm / Yangilik
# HAR DOIM `Dictation` (16:9 video sahifasi), Shorts esa `Short` (tik lenta).
#
# Yagona istisno — SHORTS bo'limiga oddiy video havolasi tushib qolsa
# (kanal `/videos` sahifasi berilgan bo'lsa): u tik shablonga tushmasligi
# uchun `Dictation` ga o'tkaziladi.
#
# > Ilgari MOVIES/CARTOONS/NEWS ham `Short` ga yozardi va katalogdan
# > "Filmlar"ga qo'shilgan oddiy video vertikal lentada, o'ziga mos
# > kelmaydigan ko'rinishda chiqardi ("na video na shorts").
_TARGET_MAP = {
    ChannelIngestTask.TargetKind.SHORTS: ("short", Short.ContentType.SHORT),
    ChannelIngestTask.TargetKind.MOVIES: ("dictation", Dictation.Type.MOVIE),
    ChannelIngestTask.TargetKind.CARTOONS: ("dictation", Dictation.Type.CARTOON),
    ChannelIngestTask.TargetKind.NEWS: ("dictation", Dictation.Type.NEWS),
    ChannelIngestTask.TargetKind.RANDOM_VIDEOS: ("dictation", Dictation.Type.RANDOM_VIDEO),
}


def pick_target(target_kind: str, url: str) -> tuple[str, str] | None:
    """Bo'lim (+ zaxira sifatida havola) → (model, subtype).

    >>> pick_target("movies", "https://www.youtube.com/watch?v=abc12345678")
    ('dictation', 'movie')
    >>> pick_target("shorts", "https://youtube.com/shorts/abc12345678")
    ('short', 'short')
    >>> pick_target("shorts", "https://www.youtube.com/watch?v=abc12345678")
    ('dictation', 'random_video')
    """
    target = _TARGET_MAP.get(target_kind)
    if target is None:
        return None
    # Shorts bo'limiga uzun video tushib qolsa — u tik shablonga tushmasin.
    if target[0] == "short" and not is_shorts_url(url):
        return ("dictation", Dictation.Type.RANDOM_VIDEO)
    return target


logger = logging.getLogger(__name__)


# `count` ga yetish uchun kanaldan buncha ko'p video ro'yxatlaymiz. Dublikatlar
# tashlab yuborilishi mumkin — ozroq zaxira bilan olamiz.
_LIST_MULTIPLIER = 5
_LIST_HARD_CAP = 300


class ChannelIngestError(Exception):
    """Kanal ochilmadi / URL noto'g'ri / API bo'sh — foydalanuvchiga tarjima qilinadi."""


def _list_channel_videos(channel_url: str, want: int) -> list[dict]:
    """`yt-dlp --flat-playlist` — eng yangi videolar ro'yxati.

    Har entry: `{"id": "...", "title": "...", "duration": ..., "url": "..."}`.
    Xato bo'lsa `ChannelIngestError` — quyi darajadagi log/`err.msg` tarjima
    qilinadi.
    """
    try:
        import yt_dlp
    except ImportError as exc:
        raise ChannelIngestError("yt-dlp o'rnatilmagan") from exc

    limit = min(_LIST_HARD_CAP, max(want, want * _LIST_MULTIPLIER))
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,           # meta'ni tez oladi, video yuklab olmaydi
        "skip_download": True,
        "playlistend": limit,
        # Kanalni turli formatda qabul qilishi uchun — /videos, /shorts, /streams
        # foydalanuvchi bergan URL o'zi hurmat qilinadi.
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(channel_url, download=False)
    except Exception as exc:                      # yt-dlp DownloadError va h.k.
        text = str(exc)
        friendly = _friendly_ytdlp(text)
        raise ChannelIngestError(friendly) from exc

    if not info:
        raise ChannelIngestError("Kanal bo'sh yoki ochib bo'lmadi")

    # Kanal URL'i: `info["entries"]` — video ro'yxati. Kanalning tab'lari
    # (/videos, /shorts, ...) `entries` ichida yana `entries` bo'lishi mumkin.
    entries = list(_flatten_entries(info))
    videos: list[dict] = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        vid = e.get("id") or ""
        # yt-dlp ba'zan channel-tab yoki playlist entrylarini kiritadi.
        # Faqat 11 belgili yalang video ID lar bizga kerak.
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", vid):
            continue
        videos.append({
            "id": vid,
            "title": (e.get("title") or "")[:250],
            "duration": int(e.get("duration") or 0),
            "url": e.get("url") or f"https://www.youtube.com/watch?v={vid}",
        })
    if not videos:
        raise ChannelIngestError("Kanaldan hech qanday video topilmadi")
    return videos


def _flatten_entries(node):
    """`extract_info` chuqur nested tuzilishga ega bo'lishi mumkin — flatten."""
    if not isinstance(node, dict):
        return
    entries = node.get("entries")
    if not entries:
        # Bu leaf video entry
        if node.get("id"):
            yield node
        return
    for child in entries:
        yield from _flatten_entries(child)


def _friendly_ytdlp(text: str) -> str:
    low = (text or "").lower()
    if "private" in low: return "Kanal xususiy — ochib bo'lmaydi"
    if "does not exist" in low or "unavailable" in low: return "Kanal mavjud emas"
    if "sign in" in low or "cookie" in low: return "Kanal login talab qilyapti"
    if "429" in low or "too many requests" in low: return "YouTube rate limit — biroz kutib qayta urining"
    if "http error 404" in low: return "Kanal URL topilmadi (404)"
    return (text or "yt-dlp xatosi")[:400]


def _video_exists(youtube_id: str) -> bool:
    """Bazada bu YouTube ID allaqachon bormi (Short YOKI Dictation)."""
    if Short.objects.filter(youtube_id=youtube_id).exists():
        return True
    # Dictation `youtube_link` da to'liq URL saqlaydi — ID icontains bilan qidiramiz.
    if Dictation.objects.filter(youtube_link__icontains=youtube_id).exists():
        return True
    return False


def _create_short(video: dict, content_type: str) -> Short:
    """Yangi Short yozuvi. Signal post_save AIJob(whisper) ni o'zi qo'yadi."""
    return Short.objects.create(
        youtube_link=video["url"],
        youtube_id=video["id"],
        content_type=content_type,
        title=video["title"] or f"Video {video['id']}",
        duration_sec=video["duration"] or 0,
        # Whisper/Haiku tayyor bo'lgach ko'rinsin — hozir yashiring.
        is_published=False,
    )


def _create_dictation(video: dict, dict_type: str) -> Dictation:
    """Yangi Dictation yozuvi (`is_media=True`, YouTube link). Signal
    post_save AIJob(whisper) va keyin haiku ni o'zi qo'yadi."""
    return Dictation.objects.create(
        title=video["title"] or f"Video {video['id']}",
        type=dict_type,
        is_media=True,
        youtube_link=video["url"],
        is_published=False,
    )


def run_ingest(task: ChannelIngestTask) -> None:
    """Bir kanal ingest task'ni bajaradi.

    Task holati va yozuvlari `save()` orqali yangilanadi. Xato bo'lsa
    `Exception` chiqadi — worker'ni ushlaydi va `failed` ga o'tadi.
    """
    task.status = ChannelIngestTask.Status.RUNNING
    task.started_at = timezone.now()
    task.error = ""
    task.videos_created = []
    task.videos_skipped = []
    task.save(update_fields=["status", "started_at", "error",
                             "videos_created", "videos_skipped", "updated_at"])

    videos = _list_channel_videos(task.channel_url, task.count)

    if task.target_kind not in _TARGET_MAP:
        raise ChannelIngestError(
            f"Noma'lum bo'lim: {task.target_kind}. Faqat: "
            + ", ".join(_TARGET_MAP.keys())
        )

    created: list[dict] = []
    skipped: list[dict] = []

    for video in videos:
        if len(created) >= task.count:
            break
        yid = video["id"]
        if _video_exists(yid):
            skipped.append({"youtube_id": yid, "title": video["title"],
                            "reason": "already-in-db"})
            continue
        # Model HAR VIDEO uchun alohida tanlanadi: kanalda tik ham, keng
        # ham bo'lishi mumkin.
        target = pick_target(task.target_kind, video["url"])
        if target is None:                        # yuqorida tekshirilgan
            continue
        model_kind, subtype = target
        try:
            if model_kind == "dictation":
                obj = _create_dictation(video, subtype)
            else:
                obj = _create_short(video, subtype)
        except Exception as exc:                  # noqa: BLE001 — DB xatosi
            logger.exception("channel ingest create failed for %s", yid)
            skipped.append({"youtube_id": yid, "title": video["title"],
                            "reason": f"create-failed: {exc}"[:200]})
            continue
        created.append({
            "youtube_id": yid, "title": video["title"],
            "target_id": obj.pk, "target_kind": model_kind,
        })

    # Katalog tugaganmi — count'ga yetmadimi?
    reached = len(created) >= task.count
    task.status = (ChannelIngestTask.Status.DONE if reached
                   else ChannelIngestTask.Status.PARTIAL)
    task.videos_created = created
    task.videos_skipped = skipped
    task.finished_at = timezone.now()
    task.save(update_fields=["status", "videos_created", "videos_skipped",
                             "finished_at", "updated_at"])

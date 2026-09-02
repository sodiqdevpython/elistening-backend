"""Fake ma'lumotlar — Movies/Songs/News/Videos uchun.

Backend hozircha faqat `Dictation` modelini haqiqiy saqlaydi. Boshqa
sahifalar (Filmlar, Qo'shiqlar, Yangiliklar, ...) bo'sh ko'rinmasin uchun
static demo ma'lumotlar shu yerda. Kelajakda haqiqiy modellar yozilganda
`legacy_views.py` shu ma'lumotlarni haqiqiy so'rovlar bilan almashtiradi.
"""
import re

from django.utils import timezone

from .models import Dictation

# --- Kategoriya metadata — sayt navigatsiyasidagi mavzular ---------------
# slug -> (name_uz, name_en, cefr_min, cefr_max, icon, color, has_video,
#          matching_dictation_type or None)
CATEGORY_META = [
    ("short-stories",   "Short Stories", "Short Stories", "A1", "C1", "book", "green",   False, Dictation.Type.SHORT_STORY),
    ("conversations",   "Suhbatlar", "Conversations",     "A1", "B1", "chat", "blue",    False, Dictation.Type.CONVERSATION),
    ("toeic-listening", "TOEIC Listening", "TOEIC Listening", "A2", "C1", "headphone", "green", False, Dictation.Type.TOEIC),
    ("ielts-listening", "IELTS Listening", "IELTS Listening", "B1", "C1", "headphone", "blue",  False, Dictation.Type.IELTS),
    ("random-videos",   "Tasodifiy videolar", "Random Videos", "B1", "C2", "play", "green", True, Dictation.Type.RANDOM_VIDEO),
    ("news",            "Yangiliklar", "News", "B1", "C1", "play", "blue",  True,  Dictation.Type.NEWS),
    ("ted",             "TED", "TED",             "C1", "C2", "play", "green", True, Dictation.Type.TED),
    ("toefl-listening", "TOEFL Listening", "TOEFL Listening", "B1", "C2", "headphone", "blue", False, Dictation.Type.TOEFL),
    ("numbers",         "Raqamlar", "Numbers",   "A1", "A1", "hash", "green", False, Dictation.Type.NUMBER),
    ("spelling-names",  "Ismlarni harflab yozish", "Spelling Names", "A1", "A1", "hash", "blue", False, Dictation.Type.SPELLING),
]


def build_categories() -> list[dict]:
    """Kategoriyalar ro'yxati — har birida haqiqiy diktantlar soni."""
    result = []
    for order, (slug, uz, en, cmin, cmax, icon, color, video, dict_type) in enumerate(CATEGORY_META):
        count = Dictation.objects.filter(type=dict_type, is_published=True).count() if dict_type else 0
        result.append({
            "id": order + 1, "slug": slug, "name_uz": uz, "name_en": en,
            "description_uz": "", "description_en": "",
            "icon": icon, "color": color, "cefr_min": cmin, "cefr_max": cmax,
            "levels": f"{cmin}–{cmax}", "has_video": video,
            "lessons_count": count, "order": order,
        })
    return result


def category_by_slug(slug: str) -> dict | None:
    for order, meta in enumerate(CATEGORY_META):
        if meta[0] == slug:
            cats = build_categories()
            return cats[order]
    return None


def dictation_type_for_slug(slug: str):
    for meta in CATEGORY_META:
        if meta[0] == slug:
            return meta[-1]
    return None


_YOUTUBE_ID_PATTERNS = [
    re.compile(r"[?&]v=([A-Za-z0-9_-]{11})"),
    re.compile(r"youtu\.be/([A-Za-z0-9_-]{11})"),
    re.compile(r"/(?:shorts|embed)/([A-Za-z0-9_-]{11})"),
]


def extract_youtube_id(url: str | None) -> str | None:
    """YouTube URL turlaridan (watch?v=, youtu.be/, /shorts/, /embed/) 11-belgili
    video ID ni chiqaradi. Topilmasa None. Frontend cards `i.ytimg.com` dan
    thumbnail yuklashi uchun ishlatiladi."""
    if not url:
        return None
    for pat in _YOUTUBE_ID_PATTERNS:
        m = pat.search(url)
        if m:
            return m.group(1)
    return None


def dictation_as_content_item(
    d: Dictation, category_slug: str = None, my_progress_percent: int | None = None,
) -> dict:
    """Dictation'ni frontend ContentItem shape'iga aylantiradi.

    `my_progress_percent` — kirgan foydalanuvchi shu diktantni ishlagan
    bo'lsa foizi. Frontend list kartochkasida "ishlangan X%" badge ko'rsatadi
    va detail sahifada "Qayta ishlash" tugmasi paydo bo'ladi.
    """
    return {
        "id": d.id, "slug": d.slug, "kind": "lesson", "title": d.title,
        "description": f"{d.get_type_display()} · {d.chunks_count} chunk"
                       if d.chunks_count else d.get_type_display(),
        "youtube_id": extract_youtube_id(d.youtube_link) if d.is_media else None,
        "duration_sec": d.duration_sec, "duration_label": d.duration_label,
        "cefr_level": d.cefr_level,
        "category": 1, "category_slug": category_slug, "group": None,
        "tags": [], "speaker_gender": "unknown", "accent": "unknown",
        "is_featured": False,
        "published_at": (d.created_at.isoformat() if d.created_at else None),
        "thumb_gradient": "linear-gradient(135deg,#2563EB 0%,#1D4ED8 100%)",
        "accent_from": "#2563EB", "accent_to": "#1D4ED8",
        "thumbnail_url": None,
        "audio_url": d.audio.url if d.audio else None,
        "artist": "", "genre": "", "source": "", "summary": "",
        "likes": 0, "dislikes": 0,
        "my_progress_percent": my_progress_percent,
    }


# --- Fake filmlar ---------------------------------------------------------
FAKE_MOVIES = [
    ("Finding Nemo — Marlin's promise", "A2", "Animatsiya", 222, True,  ("#10B981", "#16A34A"), "1lm7hqXo9Sk"),
    ("The Social Network — opening scene", "B2", "Drama", 310, False, ("#2563EB", "#1D4ED8"), "8HooRp7iNBg"),
    ("Inside Out — meet Joy", "A1", "Animatsiya", 178, True,  ("#3B82F6", "#2563EB"), "seMwpP0yeu4"),
    ("Zootopia — the hustle", "A2", "Animatsiya", 255, True,  ("#0EA5E9", "#0284C7"), "jWM0ct-OLsM"),
    ("The King's Speech — first broadcast", "B1", "Drama", 210, False, ("#64748B", "#475569"), "EDf3ZRuBLuk"),
    ("Interstellar — docking scene", "B2", "Sci-Fi", 290, False, ("#1E3A8A", "#1E40AF"), "GJT3TjIgBIA"),
    ("Paddington — marmalade sandwich", "A1", "Animatsiya", 140, True,  ("#10B981", "#059669"), "gscbFJ1WQoo"),
    ("The Pursuit of Happyness — job interview", "B1", "Drama", 238, False, ("#10B981", "#059669"), "89Kq8SDyvfg"),
]


def build_movies(kind_in: str = "movie,cartoon", search: str = "", level: str = "") -> list[dict]:
    kinds = set(kind_in.split(",")) if kind_in else {"movie", "cartoon"}
    items = []
    for i, (title, lvl, genre, dur, is_cartoon, grad, yt) in enumerate(FAKE_MOVIES):
        kind = "cartoon" if is_cartoon else "movie"
        if kind not in kinds: continue
        if level and level != "all" and lvl != level: continue
        if search and search.lower() not in title.lower(): continue
        items.append({
            "id": 1000 + i, "kind": kind, "title": title,
            "description": f"{genre} · {lvl}",
            "youtube_id": yt, "duration_sec": dur,
            "duration_label": f"{dur // 60}:{dur % 60:02d}",
            "cefr_level": lvl, "category": None, "category_slug": None, "group": None,
            "tags": [], "speaker_gender": "mixed", "accent": "american",
            "is_featured": False, "published_at": None,
            "thumb_gradient": f"linear-gradient(135deg,{grad[0]} 0%,{grad[1]} 100%)",
            "accent_from": grad[0], "accent_to": grad[1],
            "thumbnail_url": f"https://img.youtube.com/vi/{yt}/mqdefault.jpg",
            "audio_url": None, "artist": "", "genre": genre,
            "source": "", "summary": "",
            "likes": 300 + i * 40, "dislikes": 5 + i,
        })
    return items


# --- Fake yangiliklar -----------------------------------------------------
FAKE_NEWS = [
    ("New Species Discovered in Deep Ocean", "BBC Learning English", "B1", 105, 2,
     "Scientists have found a new species of fish living near an underwater volcano in the Pacific Ocean."),
    ("City Introduces Free Electric Buses", "VOA Learning English", "A2", 92, 6,
     "A European city launched a fleet of free electric buses to reduce air pollution in the centre."),
    ("Ancient Library Found Under a School", "BBC Learning English", "B2", 118, 20,
     "Builders working on a school extension uncovered the remains of a library almost two thousand years old."),
    ("Robots Help Farmers Pick Fruit", "VOA Learning English", "B1", 99, 30,
     "Farms in several countries are testing robots that can pick soft fruit without damaging it."),
    ("Students Build a Solar Powered Boat", "BBC Learning English", "B2", 130, 48,
     "A team of engineering students built a boat that runs entirely on solar power and crossed a large lake."),
    ("Rare Bird Returns After Fifty Years", "VOA Learning English", "A2", 88, 72,
     "A bird species not seen in the region for fifty years has been spotted again near a protected wetland."),
    ("New App Teaches Sign Language", "BBC Learning English", "B1", 96, 96,
     "A free app uses short videos to teach sign language to beginners."),
    ("Museum Opens at Midnight for Students", "VOA Learning English", "A2", 84, 120,
     "A national museum stayed open until midnight so that students could visit after their exams."),
]


def build_news() -> list[dict]:
    from datetime import timedelta
    now = timezone.now()
    items = []
    for i, (title, source, lvl, dur, hours_ago, summary) in enumerate(FAKE_NEWS):
        items.append({
            "id": 3000 + i, "kind": "news", "title": title,
            "description": summary[:120],
            "youtube_id": None, "duration_sec": dur,
            "duration_label": f"{dur // 60}:{dur % 60:02d}",
            "cefr_level": lvl, "category": None, "category_slug": "news", "group": None,
            "tags": ["news"], "speaker_gender": "mixed", "accent": "british",
            "is_featured": hours_ago <= 48,
            "published_at": (now - timedelta(hours=hours_ago)).isoformat(),
            "thumb_gradient": "linear-gradient(135deg,#1E293B 0%,#0F172A 100%)",
            "accent_from": "#1E293B", "accent_to": "#0F172A",
            "thumbnail_url": None, "audio_url": None,
            "artist": "", "genre": "", "source": source, "summary": summary,
            "likes": 0, "dislikes": 0,
        })
    return items


# --- Fake tasodifiy videolar (Random Videos) -----------------------------
FAKE_VIDEOS = [
    ("A Day in Tokyo", "B2", 1204, "kQdT97Dqe0Q"),
    ("How Coffee Is Made", "A2", 519, "wZeDBRpAd7Y"),
    ("The Basics of Time Management", "B1", 645, "iONDebHX9qk"),
    ("Learning New Languages Fast", "B1", 720, "d0yGdNEWdn0"),
    ("Photography Tips for Beginners", "A2", 480, "V-YSl1LlSGE"),
    ("Introduction to Cooking", "A1", 360, "S4hxWXPHZ_c"),
]


def build_videos(search: str = "", level: str = "") -> list[dict]:
    items = []
    for i, (title, lvl, dur, yt) in enumerate(FAKE_VIDEOS):
        if level and level != "all" and lvl != level: continue
        if search and search.lower() not in title.lower(): continue
        items.append({
            "id": 4000 + i, "kind": "video", "title": title,
            "description": f"YouTube video · {lvl}",
            "youtube_id": yt, "duration_sec": dur,
            "duration_label": f"{dur // 60}:{dur % 60:02d}",
            "cefr_level": lvl, "category": None, "category_slug": None, "group": None,
            "tags": [], "speaker_gender": "unknown", "accent": "american",
            "is_featured": False, "published_at": None,
            "thumb_gradient": "linear-gradient(135deg,#10B981 0%,#059669 100%)",
            "accent_from": "#10B981", "accent_to": "#059669",
            "thumbnail_url": f"https://img.youtube.com/vi/{yt}/mqdefault.jpg",
            "audio_url": None, "artist": "", "genre": "",
            "source": "", "summary": "", "likes": 0, "dislikes": 0,
        })
    return items


# --- Fake shorts ---------------------------------------------------------
FAKE_SHORTS = [
    ("Oldest man dies at 112", "B1", "male", "dQw4w9WgXcQ"),
    ("How coffee is made in 60 seconds", "A2", "female", "3JZ_D3ELwOQ"),
    ("A day in Tokyo — sunset", "B2", "mixed", "jNQXAC9IVRw"),
    ("Language hacks in 30 seconds", "B1", "female", "L_jWHffIx5E"),
    ("Photography 101", "A2", "male", "9bZkp7q19f0"),
]


def build_shorts(level: str = "", gender: str = "") -> list[dict]:
    items = []
    for i, (title, lvl, gnd, yt) in enumerate(FAKE_SHORTS):
        if level and level != "all" and lvl != level: continue
        if gender and gender != "all" and gnd != gender: continue
        items.append({
            "id": 5000 + i, "kind": "short", "title": title,
            "description": "", "youtube_id": yt, "duration_sec": 45,
            "duration_label": "0:45",
            "cefr_level": lvl, "category": None, "category_slug": None, "group": None,
            "tags": [], "speaker_gender": gnd, "accent": "american",
            "is_featured": False, "published_at": None,
            "thumb_gradient": "linear-gradient(135deg,#DB2777 0%,#BE185D 100%)",
            "accent_from": "#DB2777", "accent_to": "#BE185D",
            "thumbnail_url": f"https://img.youtube.com/vi/{yt}/mqdefault.jpg",
            "audio_url": None, "artist": "", "genre": "",
            "source": "", "summary": "",
            "likes": 100 + i * 30, "dislikes": 3 + i,
            "segments": [], "exercises": [], "vocab_items": [],
            "dictation_enabled": False, "my_reaction": None, "my_progress": None,
            "stats": {"views": 0, "likes": 100 + i * 30, "dislikes": 3 + i, "completions": 0},
        })
    return items

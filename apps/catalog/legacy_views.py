"""Frontend eski chaqiruvlari uchun view'lar.

Backend hozircha faqat `Dictation` modelini haqiqiy saqlaydi. Boshqa
kontent turlari (Filmlar, Qo'shiqlar, Yangiliklar, Videolar, Shorts,
IELTS) uchun `mock_data.py` fake ma'lumot beradi — sahifalar bo'sh
ko'rinmasin.

Kelajakda haqiqiy modellar yozilganda bu view'lar shu modellarga
ulanadi.
"""
from django.core.cache import cache
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from . import mock_data
from .models import Dictation

# Kesh TTL'lari (soniya). Bu endpointlar foydalanuvchiga bog'liq EMAS —
# aggregatsiya og'ir (counts, karusel), lekin kamdan-kam o'zgaradi. Redis
# bo'lsa (prod) haqiqiy kesh, bo'lmasa (dev) locmem — ikkalasi ham ishlaydi.
CACHE_TTL_HOME = 60
CACHE_TTL_CATEGORIES = 120
CACHE_TTL_CONFIG = 300


def _paginated(results: list) -> dict:
    return {
        "count": len(results), "page": 1, "page_size": len(results) or 20,
        "total_pages": 1, "next": None, "previous": None, "results": results,
    }


@api_view(["GET"])
@permission_classes([AllowAny])
def site_config(request):
    """Global sayt sozlamalari — navbar har sahifada o'qiydi (yengil).

    Hozircha faqat "Bog'lanish" Telegram username. Bo'sh bo'lsa frontend
    "Bog'lanish" menyusini ko'rsatmaydi.
    """
    from apps.common.models import SiteConfig, cls_cache_key
    payload = cache.get(cls_cache_key())
    if payload is None:
        cfg = SiteConfig.get_solo()
        payload = {"contact_telegram": cfg.contact_telegram or ""}
        cache.set(cls_cache_key(), payload, CACHE_TTL_CONFIG)
    return Response(payload)


@api_view(["GET"])
@permission_classes([AllowAny])
def app_ad(request):
    """Mobil ilova ochilganda ko'rsatiladigan FAOL reklama (bitta).

    Reklama bo'lmasa `{"ad": null}`. Rasm URL'i mutlaq (mobil ilova to'g'ridan
    ochadi). Keshlanmaydi — admin yoqishi bilan darrov chiqsin.
    """
    from apps.common.models import AppAd
    ad = AppAd.objects.filter(is_active=True).order_by("-created_at").first()
    if ad is None:
        return Response({"ad": None})
    image_url = ""
    if ad.image:
        try:
            image_url = request.build_absolute_uri(ad.image.url)
        except Exception:
            image_url = ad.image.url
    return Response({"ad": {
        "id": ad.id,
        "image_url": image_url,
        "title": ad.title or "",
        "body": ad.body or "",
        "link_url": ad.link_url or "",
        "duration_sec": ad.duration_sec or 0,
    }})


@api_view(["GET"])
@permission_classes([AllowAny])
def home(request):
    """Bosh sahifa: 6 mavzu kartochkasi + eng oxirgi 5 ta yangilik karuseli.

    Karusel manbasi — real DB'dagi `Dictation.type='news'` yozuvlar (chop
    etilgan, YouTube linkli). Bo'sh bo'lsa eski mock ma'lumot.
    """
    payload = cache.get("home_payload_v1")
    if payload is not None:
        return Response(payload)

    news_qs = (
        Dictation.objects
        .filter(type=Dictation.Type.NEWS, is_published=True)
        .exclude(youtube_link="")
        .exclude(body=[])   # AI transkript tayyor bo'lgangina
        .order_by("-created_at")[:5]
    )
    carousel = [
        {
            "id": d.id,
            "slug": d.slug,
            "title": d.title,
            "youtube_id": mock_data.extract_youtube_id(d.youtube_link),
            "duration_label": d.duration_label,
            "source": d.get_type_display(),
            "cefr_level": d.cefr_level or "",
        }
        for d in news_qs
    ]
    if not carousel:
        # DB'da real yangilik yo'q — eski demo/mock ma'lumot
        carousel = mock_data.build_news()[:5]
    payload = {
        "categories": mock_data.build_categories(),
        "carousel": carousel,
    }
    cache.set("home_payload_v1", payload, CACHE_TTL_HOME)
    return Response(payload)


@api_view(["GET"])
@permission_classes([AllowAny])
def categories_list(request):
    data = cache.get("categories_v1")
    if data is None:
        data = mock_data.build_categories()
        cache.set("categories_v1", data, CACHE_TTL_CATEGORIES)
    return Response(data)


@api_view(["GET"])
@permission_classes([AllowAny])
def category_groups(request, slug):
    """Mavzu ichidagi diktantlar — haqiqiy `Dictation` ro'yxati.

    Diktant progressi ro'yxatda ko'rsatilmaydi — har chunk'da yozib borish
    hozircha DB uchun qimmat. Kartochkalar sodda: thumbnail, sarlavha, daraja.
    """
    category = mock_data.category_by_slug(slug)
    if not category:
        return Response({"detail": "Kategoriya topilmadi"}, status=404)

    dict_type = mock_data.dictation_type_for_slug(slug)
    # `?light=1` bo'lsa faqat sarlavha ma'lumotini qaytaramiz — asosiy ro'yxat
    # DictationViewSet paginated endpoint orqali infinite scroll bilan yuklanadi.
    light = request.query_params.get("light") == "1"
    lessons = []
    lessons_count = 0
    if dict_type:
        # Faqat AI transkript tayyor (body bo'sh emas) diktantlarni.
        qs = Dictation.objects.filter(
            type=dict_type, is_published=True,
        ).exclude(body=[])
        if light:
            lessons_count = qs.count()
        else:
            lessons = [
                mock_data.dictation_as_content_item(d, slug)
                for d in qs.order_by("-created_at")
            ]
            lessons_count = len(lessons)

    return Response({
        "category": category,
        "groups": [
            {"id": 1, "title": category["name_uz"], "category": category["id"],
             "order": 0, "is_open_by_default": True,
             "lessons_count": lessons_count, "lessons": lessons},
        ],
        "ungrouped": [],
    })


@api_view(["GET"])
@permission_classes([AllowAny])
def content_list(request):
    kind = request.query_params.get("kind", "")
    kind_in = request.query_params.get("kind_in", "")
    search = request.query_params.get("search", "").strip()
    level = request.query_params.get("level", "").strip()

    kinds = set()
    if kind_in: kinds.update(k.strip() for k in kind_in.split(",") if k.strip())
    if kind: kinds.add(kind)

    results = []
    if "movie" in kinds or "cartoon" in kinds:
        results += mock_data.build_movies(kind_in or kind, search, level)
    if "news" in kinds:
        results += mock_data.build_news()
    if "video" in kinds:
        results += mock_data.build_videos(search, level)
    return Response(_paginated(results))


@api_view(["GET"])
@permission_classes([AllowAny])
def content_detail(request, pk):
    """Fake ContentDetail — Movies/News/Videos uchun."""
    all_items = (mock_data.build_movies()
                 + mock_data.build_news() + mock_data.build_videos())
    item = next((x for x in all_items if x["id"] == pk), None)
    if not item:
        return Response({"detail": "Not found"}, status=404)
    item = dict(item)
    item.update({
        "segments": _fake_segments_for(item),
        "exercises": _fake_exercises_for(item),
        "vocab_items": _fake_vocab_for(item),
        "dictation_enabled": False,
        "my_reaction": None, "my_progress": None,
        "stats": {"views": 0, "likes": item["likes"], "dislikes": item["dislikes"],
                  "completions": 0},
    })
    return Response(item)


def _fake_segments_for(item):
    if item["kind"] in ("movie", "cartoon"):
        third = item["duration_sec"] // 3
        return [
            {"id": 900 + i, "index": i, "label": lbl,
             "start_ms": i * third * 1000,
             "end_ms": (item["duration_sec"] if i == 2 else (i + 1) * third) * 1000,
             "duration_label": "", "text": "", "words": [], "payload": {}}
            for i, lbl in enumerate(["Boshlanishi", "O'rtasi", "Oxiri"])
        ]
    return []


def _fake_exercises_for(item):
    if item["kind"] == "news":
        return [
            {"id": 700, "type": "true_false", "title": "The story is about science.",
             "order": 0, "difficulty": 2, "payload": {"statement": "The story is about science."},
             "segment": None},
            {"id": 701, "type": "mcq", "title": "What is the main topic?",
             "order": 1, "difficulty": 2,
             "payload": {"question": "What is the main topic?",
                         "options": ["Science and nature", "Sport", "Cooking", "Fashion"]},
             "segment": None},
        ]
    if item["kind"] in ("movie", "cartoon", "video"):
        return [
            {"id": 720 + i, "type": "short_answer",
             "title": q, "order": i, "difficulty": 2,
             "payload": {"question": q, "hint": "Bitta so'z bilan javob bering", "max_words": 3},
             "segment": None}
            for i, q in enumerate([
                "Where does the scene take place?",
                "Who is the main character?",
            ])
        ]
    return []


def _fake_vocab_for(_item):
    return []


@api_view(["GET"])
@permission_classes([AllowAny])
def shorts_feed(request):
    level = request.query_params.get("level", "")
    gender = request.query_params.get("gender", "")
    return Response({
        "next": None, "previous": None,
        "results": mock_data.build_shorts(level, gender),
    })

"""Namuna diktantlar bilan bazani to'ldiradi.

    python manage.py seed_demo          # qo'shadi / yangilaydi
    python manage.py seed_demo --fresh  # avval eski demo'ni o'chiradi

Har mavzu (type) uchun bir nechta CHOP ETILGAN namuna diktant yaratiladi
(body ichida timestamp bilan sample chunklar). Shu ma'lumot frontend
"All Topics" da guruhlar bo'yicha ko'rinadi. Tariflar, foydalanuvchilar va
reyting uchun demo ma'lumot ham qo'shiladi.
"""
import random
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import DailyActivity, User
from apps.billing.models import Plan
from apps.catalog.models import Dictation

# (code, uz, en, uzs, usd, features_uz, features_en, is_default,
#  shorts, video, dictation, ielts)  — limit: None = cheksiz, 0 = yo'q, N = kuniga
PLANS = [
    ("free", "Qaldirg'och", "Qaldirg'och", 0, 0,
     ["Kuniga 8 ta Shorts", "Kuniga 2 ta video", "Kuniga 2 ta diktant", "Reklama bilan"],
     ["8 Shorts per day", "2 videos per day", "2 dictations per day", "Ads included"],
     True, 8, 2, 2, 0),
    ("plus", "Jo'shqin", "Jo'shqin", 23000, "2.30",
     ["Kuniga 30 ta Shorts", "Kuniga 10 ta video", "Cheksiz diktant",
      "Kuniga 2 ta IELTS test", "Reklamasiz"],
     ["30 Shorts per day", "10 videos per day", "Unlimited dictation",
      "2 IELTS tests per day", "No ads"],
     False, 30, 10, None, 2),
    ("pro", "Bo'talog'im", "Bo'talog'im", 32000, "3.20",
     ["Cheksiz Shorts", "Cheksiz video", "Cheksiz diktant", "Cheksiz IELTS test", "Reklamasiz"],
     ["Unlimited Shorts", "Unlimited videos", "Unlimited dictation", "Unlimited IELTS tests", "No ads"],
     False, None, None, None, None),
]

LEADERBOARD_NAMES = [
    "Aziz Karimov", "Malika Yusupova", "Jahongir Toshev", "Nilufar Rasulova", "Sardor Aliyev",
    "Dilnoza Ergasheva", "Bekzod Xolmatov", "Zilola Nazarova", "Otabek Sattorov", "Kamola Isroilova",
    "Shohruh Qodirov", "Gulnora Ahmadova", "Islom Rahimov", "Sevara Tursunova", "Doniyor Umarov",
]

# Har mavzu (type) uchun 2-3 namuna diktant.
SAMPLE_DICTATIONS = [
    # SHORT STORIES
    (Dictation.Type.SHORT_STORY, "The Lucky Boy", "A2", False,
     "John Tenniswood, once described himself as a lucky man."),
    (Dictation.Type.SHORT_STORY, "First Snowfall", "A2", False,
     "First snowfall today is November 26th."),
    (Dictation.Type.SHORT_STORY, "The Old Bookbinder", "B1", False,
     "He worked as a bookbinder for most of his life."),
    # CONVERSATIONS
    (Dictation.Type.CONVERSATION, "At the coffee shop", "A1", False,
     "Hello, can I have a cup of coffee please?"),
    (Dictation.Type.CONVERSATION, "Booking a room", "A2", False,
     "Good morning, I would like to book a double room for two nights."),
    # NUMBERS
    (Dictation.Type.NUMBER, "Numbers 0 to 100", "A1", False,
     "One, two, three, four, five, six, seven, eight, nine, ten."),
    (Dictation.Type.NUMBER, "Phone numbers", "A1", False,
     "My phone number is oh-seven-nine-one-one, two-two-three-three-four-four."),
    # SPELLING
    (Dictation.Type.SPELLING, "Common surnames", "A1", False,
     "T-H-O-M-P-S-O-N. Thompson."),
    (Dictation.Type.SPELLING, "First names", "A1", False,
     "S-A-R-A-H. Sarah. J-A-M-E-S. James."),
    # IELTS
    (Dictation.Type.IELTS, "Cam21 - Test 1 - Part 1", "B1", False,
     "Listen to a conversation between a student and a university advisor."),
    (Dictation.Type.IELTS, "Cam21 - Test 1 - Part 2", "B2", False,
     "The following is a talk about becoming a film makeup artist."),
    # NEWS
    (Dictation.Type.NEWS, "Solar-powered boat", "B1", True,
     "A team of engineering students built a boat that runs entirely on solar power."),
    (Dictation.Type.NEWS, "Free electric buses", "A2", True,
     "The city launched a fleet of free electric buses to reduce air pollution."),
    # RANDOM VIDEO
    (Dictation.Type.RANDOM_VIDEO, "A day in Tokyo", "B2", True,
     "Welcome to Tokyo. Today we will explore the city from morning until sunset."),
    # TED
    (Dictation.Type.TED, "The power of vulnerability", "C1", True,
     "So, I'll start with this: a couple of years ago, an event planner called me."),
    # KIDS STORY
    (Dictation.Type.KIDS_STORY, "The little rabbit", "A2", False,
     "Once upon a time, there was a little rabbit who lived in a big forest."),
    # TOEIC
    (Dictation.Type.TOEIC, "Office announcement", "A2", False,
     "Attention all employees, the monthly meeting will start at three o'clock."),
    # TOEFL
    (Dictation.Type.TOEFL, "Campus discussion", "B2", False,
     "Excuse me, I need help finding the biology department office."),
]


def _build_body(sample_text: str) -> list[dict]:
    """Sample matnni gap-gap bo'lib body chunklariga aylantiradi."""
    sentences = [s.strip() for s in sample_text.replace("!", ".").replace("?", ".").split(".") if s.strip()]
    body = []
    t = 0
    for s in sentences:
        # Har so'zga ~350 ms
        dur = max(2500, len(s.split()) * 400)
        body.append({"start_ms": t, "end_ms": t + dur, "text": s + "."})
        t += dur + 400  # kichik pauza
    return body


class Command(BaseCommand):
    help = "Namuna diktantlar va foydalanuvchilar bilan bazani to'ldiradi"

    def add_arguments(self, parser):
        parser.add_argument("--fresh", action="store_true",
                            help="Avval mavjud demo diktantlarni o'chiradi")

    @transaction.atomic
    def handle(self, *args, **options):
        random.seed(42)
        if options["fresh"]:
            self.stdout.write("Eski demo diktantlar o'chirilmoqda...")
            Dictation.objects.all().delete()

        self.seed_dictations()
        self.seed_plans()
        self.seed_leaderboard_users()

        self.stdout.write(self.style.SUCCESS(
            f"\nTayyor.\n"
            f"  Diktantlar: {Dictation.objects.count()} (barchasi chop etilgan, body bilan)\n"
            f"  Tariflar: {Plan.objects.count()}\n"
            f"\nAdmin panelida audio yuklash va segmentlarni tahrirlash mumkin:\n"
            f"  /admin/catalog/dictation/\n"
        ))

    def seed_dictations(self):
        count = 0
        for type_val, title, level, is_media, sample in SAMPLE_DICTATIONS:
            body = _build_body(sample)
            Dictation.objects.update_or_create(
                title=title,
                defaults={
                    "type": type_val,
                    "cefr_level": level,
                    "is_media": is_media,
                    "youtube_link": "https://youtube.com/watch?v=dQw4w9WgXcQ" if is_media else "",
                    "body": body,
                    "is_published": True,
                    "views": random.randint(10, 500),
                    "practiced_time": random.randint(60_000, 3_600_000),
                },
            )
            count += 1
        self.stdout.write(f"  - {count} ta chop etilgan diktant (body chunklar bilan)")

    def seed_plans(self):
        for order, (code, uz, en, uzs, usd, f_uz, f_en, is_default, shorts, video, dictation, ielts) in enumerate(PLANS):
            Plan.objects.update_or_create(
                code=code,
                defaults={"name_uz": uz, "name_en": en, "price_uzs": uzs, "price_usd": usd,
                          "features_uz": f_uz, "features_en": f_en, "is_default": is_default,
                          "daily_shorts_limit": shorts, "daily_video_limit": video,
                          "daily_dictation_limit": dictation, "daily_ielts_limit": ielts,
                          "order": order, "is_active": True},
            )
        self.stdout.write(f"  - {len(PLANS)} ta tarif")

    def seed_leaderboard_users(self):
        today = timezone.localdate()
        for index, name in enumerate(LEADERBOARD_NAMES):
            user, created = User.objects.get_or_create(
                username=f"demo{index + 1}",
                defaults={"display_name": name, "cefr_level": ["A2", "B1", "B2", "C1"][index % 4]},
            )
            if created:
                user.set_unusable_password()
                user.save(update_fields=["password"])
            for day in range(30):
                seconds = random.randint(0, 5400 - index * 120)
                DailyActivity.objects.update_or_create(
                    user=user, date=today - timedelta(days=day),
                    defaults={"seconds": max(0, seconds)},
                )
            user.last_active_at = timezone.now()
            user.save(update_fields=["last_active_at"])
        self.stdout.write(f"  - {len(LEADERBOARD_NAMES)} ta demo foydalanuvchi (reyting uchun)")

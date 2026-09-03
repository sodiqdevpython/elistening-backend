"""Katalog — bitta model: Dictation.

Sodda va yagona model: har diktant yozuvi bir xil maydonlarga ega. Farqi
faqat `type` (mavzu) va `is_media` (media varianti: video havolasi qo'shiladi).

    Dictation
        title, slug, type (short_story/number/spelling/ielts/...),
        cefr_level (A1-C2), audio, body (JSON — timestamp bilan matn),
        is_media, youtube_link, views, practiced_time

    DictationProgress  — foydalanuvchi progressi (percent, oxirgi chunk).

Boshqa modellar (Segment, Exercise, ContentItem, ...) o'chirildi.
Body JSON diktant chunk'larini o'z ichiga oladi:

    [
      {"start_ms": 0,     "end_ms": 3800,  "text": "Hello there."},
      {"start_ms": 3800,  "end_ms": 8100,  "text": "How are you?"},
      ...
    ]

Admin panelidagi 🎬 Segment editor shu JSON ni waveform ustida qo'lda
tahrirlash imkonini beradi.
"""
from django.conf import settings
from django.db import models
from django.utils.text import slugify

from apps.common.models import CEFR, TimeStampedModel


# `body`, `full_text`, `words_json` maydonlari juda katta bo'lishi mumkin
# (transkripti uzun bo'lgan diktantda). Ro'yxatlarda va admin change_list'da
# ular yuklanmasin — bu manager defer() bilan avtomatik chetlashtiradi.
HEAVY_FIELDS = ("body", "full_text", "words_json")


class LightDictationManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().defer(*HEAVY_FIELDS)


class Dictation(TimeStampedModel):
    """Diktant darsi — bitta va yagona model.

    Barcha diktantlar (short stories, numbers, spellings, IELTS, news,
    conversations, ...) shu modelda saqlanadi. `type` maydoni orqali
    ular ajratiladi va sayt navigatsiyasida guruhlanadi.

    `is_media=True` bo'lganda `youtube_link` maydoni to'ldiriladi va
    frontendda audio yonida video ham ko'rsatiladi. Aks holda faqat
    audio bilan diktant.
    """

    class Type(models.TextChoices):
        SHORT_STORY = "short_story", "Short Stories"
        CONVERSATION = "conversation", "Suhbatlar"
        NUMBER = "number", "Raqamlar"
        SPELLING = "spelling", "Ismlarni harflab yozish"
        IELTS = "ielts", "IELTS Listening"
        TOEFL = "toefl", "TOEFL Listening"
        TOEIC = "toeic", "TOEIC Listening"
        NEWS = "news", "Yangiliklar"
        RANDOM_VIDEO = "random_video", "Tasodifiy videolar"
        TED = "ted", "TED"
        KIDS_STORY = "kids_story", "Bolalar uchun hikoyalar"
        # MEDICAL ("medical") va IPA ("ipa") olib tashlandi — mavzular
        # ro'yxatida kerak emas. Eski yozuvlar bazada qolishi mumkin, lekin
        # `dictation_types` endpoint'i `Type.choices` bo'yicha ishlagani sabab
        # ular sayt navigatsiyasida ko'rinmaydi.

    class TranscriptionStatus(models.TextChoices):
        IDLE = "idle", "Bo'sh"
        PROCESSING = "processing", "Ishlanmoqda"
        DONE = "done", "Tayyor"
        FAILED = "failed", "Xato"

    # Asosiy identifikatorlar
    title = models.CharField("Sarlavha", max_length=250)
    slug = models.SlugField(
        "Slug", max_length=280, unique=True, blank=True,
        help_text="Bo'sh qoldirilsa sarlavhadan avtomatik yaratiladi.",
    )
    type = models.CharField(
        "Mavzu", max_length=20, choices=Type.choices, db_index=True,
        help_text="Sayt navigatsiyasida shu bo'yicha guruhlanadi.",
    )
    cefr_level = models.CharField(
        "Daraja", max_length=2, choices=CEFR.choices, blank=True, default="", db_index=True,
        help_text="Bo'sh qoldirilsa AI transkript matnini tahlil qilib avtomatik aniqlaydi. "
                  "Qo'lda tahrirlash mumkin.",
    )

    # Manba: audio majburiy, media bo'lsa youtube_link qo'shimcha
    audio = models.FileField(
        "Audio fayl", upload_to="dictations/audio/%Y/%m/", null=True, blank=True,
        help_text="mp3 / wav. Diktant asosan shu audio bo'yicha yoziladi.",
    )
    is_media = models.BooleanField(
        "Video ham bor?", default=False, db_index=True,
        help_text="Belgilansa YouTube havolasi qo'shiladi va frontendda ko'rsatiladi.",
    )
    youtube_link = models.URLField(
        "YouTube havolasi", blank=True,
        help_text="Faqat 'Video ham bor?' belgilangan diktantlar uchun.",
    )

    # Transkript (timestamp bilan) — frontendga to'liq yuboriladi.
    # `body` = "chunk" darajasidagi segmentlar (odam qulog'iga qulay).
    # `words_json` = so'z darajasidagi timestamp (kelajakda so'z-so'z tekshiruv uchun).
    # `full_text` = to'liq transkript (formatlanmagan matn).
    # Ikkalasi ham katta bo'lishi mumkin — DBda "deferred" (defer()/only() bilan
    # ro'yxatda yuklanmaydi). Faqat batafsil ko'rinishda so'raladi.
    body = models.JSONField(
        "Transkript (timestamp bilan)", default=list, blank=True,
        help_text='[{"start_ms": 0, "end_ms": 3800, "text": "Hello there."}, ...]',
    )
    full_text = models.TextField(
        "To'liq matn (formatlanmagan)", blank=True, default="",
        help_text="Whisper'ning to'liq matni. Katta hajmga tayyor.",
    )
    words_json = models.JSONField(
        "So'z darajasidagi timestamp", default=list, blank=True,
        help_text='[{"start": 0.12, "end": 0.48, "word": "Hello"}, ...] — so\'z-so\'z ijro/tekshiruv uchun.',
    )

    # AI orqali generatsiya qilingan listening test savollari — diktantda
    # ikki rejim bo'ladi: (1) diktant (mavjud), (2) listening test (savollarga
    # javob). Bu maydonlar bo'sh bo'lsa test rejimi ko'rinmaydi.
    # Shakli — Shorts modeli bilan bir xil, shu bois QuestionsPanel qayta
    # ishlatilishi mumkin.
    mcq_questions = models.JSONField(
        "MCQ savollar (AI)", default=list, blank=True,
    )
    tfng_questions = models.JSONField(
        "TFNG savollar (AI)", default=list, blank=True,
    )
    fill_gap_questions = models.JSONField(
        "Bo'shliqni to'ldirish savollari (AI)", default=list, blank=True,
    )
    tests_status = models.CharField(
        "Test AI holati", max_length=12,
        choices=[
            ("idle", "Bo'sh"),
            ("processing", "Ishlanmoqda"),
            ("done", "Tayyor"),
            ("failed", "Xato"),
        ],
        default="idle",
    )
    tests_error = models.TextField(
        "Test yaratishdagi xato", blank=True, default="",
    )

    # AI transkripsiya holati
    transcription_status = models.CharField(
        "Transkript holati", max_length=12,
        choices=TranscriptionStatus.choices, default=TranscriptionStatus.IDLE,
        db_index=True,
    )
    transcription_error = models.TextField(
        "Oxirgi xato", blank=True, default="",
        help_text="AI to'ldirish urinishida yuz bergan xato (agar bor bo'lsa).",
    )
    audio_duration_sec = models.PositiveIntegerField(
        "Audio davomiyligi (soniya)", default=0,
        help_text="Faylni yuklaganda avtomatik o'lchanadi.",
    )

    # Chop etish va statistika
    is_published = models.BooleanField("Chop etilgan", default=True, db_index=True)
    views = models.PositiveIntegerField("Ko'rishlar", default=0)
    likes = models.PositiveIntegerField("Like'lar", default=0)
    dislikes = models.PositiveIntegerField("Dislike'lar", default=0)
    practiced_time = models.PositiveBigIntegerField(
        "Umumiy tinglagan vaqt (ms)", default=0,
        help_text="Barcha foydalanuvchilar tomonidan diktant ustida sarflangan vaqt.",
    )

    # Ikkita manager:
    #   `objects`  — barcha maydonlar bilan (batafsil ko'rinish uchun)
    #   `light`    — body/full_text/words_json siz (ro'yxatlar uchun)
    objects = models.Manager()
    light = LightDictationManager()

    class Meta:
        verbose_name = "Diktant"
        verbose_name_plural = "Diktantlar"
        ordering = ["type", "-created_at"]
        indexes = [
            models.Index(fields=["type", "is_published"]),
            models.Index(fields=["cefr_level", "is_published"]),
            models.Index(fields=["transcription_status"]),
        ]

    def __str__(self):
        return f"[{self.get_type_display()}] {self.title}"

    def save(self, *args, **kwargs):
        if not self.slug and self.title:
            base = slugify(self.title) or "dictation"
            slug = base
            # Bir xil sarlavha bo'lsa slug'ga `-YYYYMMDD-HHMM` qo'shiladi —
            # o'qish oson, tartib saqlanadi va 100% uniq.
            if Dictation.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                from django.utils import timezone
                stamp = timezone.localtime().strftime("%Y%m%d-%H%M")
                slug = f"{base}-{stamp}"
                # Juda kam holatda bir vaqtda qo'shilsa — qo'shimcha suffix
                counter = 2
                while Dictation.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                    slug = f"{base}-{stamp}-{counter}"
                    counter += 1
            self.slug = slug
        # Audio yuklanganda davomiylikni avtomatik o'lchaymiz.
        if self.audio and not self.audio_duration_sec:
            try:
                from mutagen import File as MutagenFile
                info = MutagenFile(self.audio.path)
                if info and info.info:
                    self.audio_duration_sec = int(round(info.info.length))
            except Exception:
                pass  # xato bo'lsa jim o'tib ketamiz — kritik emas
        super().save(*args, **kwargs)

    @property
    def duration_ms(self) -> int:
        """Umumiy davomiyligi — oxirgi chunk end_ms i."""
        if not self.body:
            return 0
        try:
            return max(int(chunk.get("end_ms", 0) or 0) for chunk in self.body)
        except (TypeError, ValueError):
            return 0

    @property
    def duration_sec(self) -> int:
        return self.duration_ms // 1000

    @property
    def duration_label(self) -> str:
        m, s = divmod(self.duration_sec, 60)
        return f"{m}:{s:02d}"

    @property
    def chunks_count(self) -> int:
        return len(self.body or [])


# --- Proxy modellar (admin bo'limlari) -----------------------------------
# Har mavzu uchun admin panelida alohida bo'lim. Ular Dictation ustidan
# proxy — ma'lumot bir xil jadvalda, faqat filtr va admin form o'zgacha.

class _TypedManager(models.Manager):
    """Belgilangan `type` bo'yicha avtomatik filtrlaydigan manager."""
    _forced_type = ""

    def get_queryset(self):
        qs = super().get_queryset()
        if self._forced_type:
            qs = qs.filter(type=self._forced_type)
        return qs


def _make_proxy(type_value: str, verbose: str, verbose_plural: str, order: int):
    """Har mavzu uchun proxy model yasovchi factory."""

    class Meta:
        proxy = True
        app_label = "catalog"
        verbose_name = verbose
        verbose_name_plural = verbose_plural
        # Chap navbarda tartibli chiqishi uchun
        ordering = ["-created_at"]

    class ProxyManager(_TypedManager):
        _forced_type = type_value

    def save_hook(self, *args, **kwargs):
        # Bu bo'lim orqali yaratilgan yozuvda type avtomatik o'rnatiladi
        if not self.type:
            self.type = type_value
        Dictation.save(self, *args, **kwargs)

    # Klass nomi CamelCase — admin URL uchun ishlatiladi (lowercased)
    cls_name = "".join(w.capitalize() for w in type_value.split("_")) + "Dictation"
    attrs = {
        "__module__": __name__,
        "Meta": Meta,
        "objects": ProxyManager(),
        "save": save_hook,
        "_admin_order": order,
        "_admin_media_kind": (
            "video" if type_value in ("news", "random_video") else "audio"
        ),
    }
    return type(cls_name, (Dictation,), attrs)


# 9 ta proxy — foydalanuvchi so'ragan tartibda.
ShortStoryDictation = _make_proxy(
    "short_story", "1. Short Story", "1. Short Stories", 1,
)
ConversationDictation = _make_proxy(
    "conversation", "2. Suhbat", "2. Suhbatlar (Conversations)", 2,
)
ToeicListeningDictation = _make_proxy(
    "toeic", "3. TOEIC Listening", "3. TOEIC Listening", 3,
)
IeltsListeningDictation = _make_proxy(
    "ielts", "4. IELTS Listening", "4. IELTS Listening", 4,
)
RandomVideoDictation = _make_proxy(
    "random_video", "5. Tasodifiy video", "5. Random Videos (video)", 5,
)
NewsDictation = _make_proxy(
    "news", "6. Yangilik (video)", "6. News (video)", 6,
)
ToeflListeningDictation = _make_proxy(
    "toefl", "7. TOEFL Listening", "7. TOEFL Listening", 7,
)
NumbersDictation = _make_proxy(
    "number", "8. Raqamlar", "8. Numbers", 8,
)
SpellingNamesDictation = _make_proxy(
    "spelling", "9. Harflab yozish", "9. Spelling Names", 9,
)


DICTATION_PROXIES = [
    ShortStoryDictation, ConversationDictation, ToeicListeningDictation,
    IeltsListeningDictation, RandomVideoDictation, NewsDictation,
    ToeflListeningDictation, NumbersDictation, SpellingNamesDictation,
]


class Short(TimeStampedModel):
    """YouTube Shorts uslubidagi qisqa video + AI-generatsiya qilingan savollar.

    Admin faqat `youtube_link` kiritadi — qolgan hamma narsa avtomatik:
      1. `yt-dlp` audio va meta ma'lumot yuklaydi (title, duration)
      2. Whisper transkript qiladi (`full_text`, `words_json`)
      3. Claude Haiku prompt bilan CEFR daraja, teglar, 2 MCQ, 2 TFNG savol
         (True / False / Not given) yaratadi
      4. Foydalanuvchi shorts feed'da video ustida javob beradi

    Har savolda `proof_from_text` — timestamp bilan iqtibos. Frontendda
    "isbot" tugmasi shu vaqtga videoni suradi.
    """

    class TranscriptionStatus(models.TextChoices):
        IDLE = "idle", "Bo'sh"
        PROCESSING = "processing", "Ishlanmoqda"
        DONE = "done", "Tayyor"
        FAILED = "failed", "Xato"

    class ContentType(models.TextChoices):
        SHORT = "short", "Short (qisqa)"
        NEWS = "news", "Yangilik"
        CARTOON = "cartoon", "Multfilm"
        MOVIE = "movie", "Film / uzun video"

    # Kontent turi — bir necha frontend feed'lariga ajratish uchun.
    # `short` (default) — /shorts feed'ida; boshqalar /news, /movies, /cartoons.
    # AI pipeline barchasi uchun bir xil (Whisper + Claude).
    content_type = models.CharField(
        "Kontent turi", max_length=10, choices=ContentType.choices,
        default=ContentType.SHORT, db_index=True,
    )

    # Asosiy — user faqat linkni beradi
    youtube_link = models.URLField(
        "YouTube havolasi", unique=True,
        help_text="YouTube Shorts URL (masalan: https://youtube.com/shorts/XXXXXXXXXXX). "
                  "Qolgani AI orqali avtomatik to'ldiriladi.",
    )
    youtube_id = models.CharField(
        "YouTube ID (avto)", max_length=16, blank=True, default="", db_index=True,
        help_text="URL'dan avtomatik ajratiladi.",
    )
    title = models.CharField(
        "Sarlavha (avto)", max_length=250, blank=True, default="",
        help_text="AI to'ldirsa YouTube video sarlavhasi olinadi.",
    )
    duration_sec = models.PositiveIntegerField("Davomiylik (soniya)", default=0)

    # AI natijalari
    full_text = models.TextField(
        "To'liq transkript", blank=True, default="",
        help_text="Whisper natijasi — timestamp'lar bilan formatlangan.",
    )
    words_json = models.JSONField(
        "So'z darajasidagi timestamp", default=list, blank=True,
        help_text='[{"start": 0.12, "end": 0.48, "word": "Hello"}, ...]',
    )
    cefr_from = models.CharField("CEFR (min)", max_length=2, blank=True, default="")
    cefr_to = models.CharField("CEFR (max)", max_length=2, blank=True, default="")
    tags = models.JSONField(
        "Teglar (10 ta)", default=list, blank=True,
        help_text="AI aniqlagan 10 ta mavzu tegi.",
    )
    mcq_questions = models.JSONField(
        "Multiple choice savollar", default=list, blank=True,
        help_text='[{"question": "...", "options": {"A": "...", "B": "...", "C": "...", "D": "..."}, '
                  '"answer": "A", "proof_from_text": "[12.3] iqtibos"}, ...]',
    )
    tfng_questions = models.JSONField(
        "True/False/Not given savollar", default=list, blank=True,
        help_text='[{"question": "...", "answer": "True"|"False"|"Not given", '
                  '"proof_from_text": "[12.3] iqtibos"}, ...]',
    )
    fill_gap_questions = models.JSONField(
        "Bo'shliqni to'ldirish savollari", default=list, blank=True,
        help_text='[{"sentence": "He wants to ___ home.", "answer": "call", '
                  '"proof_from_text": "[3.4] iqtibos"}, ...]',
    )

    # AI ish holati
    transcription_status = models.CharField(
        "AI holati", max_length=12, choices=TranscriptionStatus.choices,
        default=TranscriptionStatus.IDLE, db_index=True,
    )
    transcription_error = models.TextField(
        "Oxirgi xato", blank=True, default="",
    )

    # Chop etish + statistika
    is_published = models.BooleanField("Chop etilgan", default=True, db_index=True)
    # YouTube tomonidan o'chirilgan / xususiy / mavjud emas — frontend player
    # xato bergach shu bayroq yoqiladi. Feed'da bu videolar ko'rsatilmaydi va
    # admin bulk amal bilan ularni butunlay o'chirishi mumkin.
    is_dead = models.BooleanField("Video o'lik (YouTube'da yo'q)", default=False, db_index=True)
    dead_reported_at = models.DateTimeField(
        "Oxirgi 'o'lik' xabari", null=True, blank=True,
    )
    dead_report_count = models.PositiveIntegerField(
        "'O'lik' xabarlar soni", default=0,
    )
    views = models.PositiveIntegerField("Ko'rishlar", default=0)
    likes = models.PositiveIntegerField("Like'lar", default=0)
    dislikes = models.PositiveIntegerField("Dislike'lar", default=0)

    # Qo'lda beriladigan ustuvorlik. Feed **qat'iy pog'ona** bo'yicha ishlaydi:
    # avval priority=10 lar (o'zaro tasodifiy), keyin 9 lar, ... oxirida 0 lar.
    # Foydalanuvchi ko'rgan videolar feed so'rovida `exclude=` bilan tashlab
    # yuborilgani sabab, yuqori priority'li KO'RILMAGAN video har doim
    # lentaning boshida chiqadi.
    priority = models.PositiveSmallIntegerField(
        "Ustuvorlik", default=0, db_index=True,
        help_text="0 = oddiy. Katta raqam = lentada oldinroq chiqadi "
                  "(10 gacha). Bir xil raqamlilar o'zaro tasodifiy tartibda.",
    )

    class Meta:
        # Menuda "Umumiy videolar" nomi ostida chiqadi — bir joyda barcha
        # turdagi videolarni (shorts + news + movies + cartoons) ko'rish va
        # `content_type` ni qatorda almashtirish uchun. Alohida turlar uchun
        # SHORT_PROXIES bo'limlari bor (yuqorida ro'yxatda 5-8 tartibda).
        verbose_name = "5. Shorts (barcha turlari)"
        verbose_name_plural = "5. Shorts — barchasi (tur almashtirish)"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["is_published", "-created_at"]),
            models.Index(fields=["content_type", "is_published"]),
            # Feed so'rovi: published + tirik + tayyor → priority bo'yicha saralash.
            models.Index(fields=["-priority", "-created_at"]),
        ]

    def __str__(self):
        return self.title or (self.youtube_id and f"[Short {self.youtube_id}]") or f"Short #{self.pk}"


# --- Short proxy modellar (admin bo'limlari) -----------------------------
# `Short.content_type` bo'yicha ajratilgan bo'limlar — admin har turga alohida
# "Qo'shish" tugmasi bilan ishlashi uchun. `Dictation` proxy'lari bilan bir
# xil pattern. Ma'lumot bir jadvalda, faqat filtr va default `content_type`
# farq qiladi.

class _ContentTypedShortManager(models.Manager):
    """Belgilangan `content_type` bo'yicha avtomatik filtrlaydigan manager."""
    _forced_content_type = ""

    def get_queryset(self):
        qs = super().get_queryset()
        if self._forced_content_type:
            qs = qs.filter(content_type=self._forced_content_type)
        return qs


def _make_short_proxy(content_type_value: str, verbose: str, verbose_plural: str, order: int):
    """Har `content_type` uchun Short proxy yasovchi factory."""

    class Meta:
        proxy = True
        app_label = "catalog"
        verbose_name = verbose
        verbose_name_plural = verbose_plural
        ordering = ["-created_at"]

    class ProxyManager(_ContentTypedShortManager):
        _forced_content_type = content_type_value

    def save_hook(self, *args, **kwargs):
        # Proxy orqali yaratilgan yozuv HAR DOIM shu content_type ga tegishli.
        # (Model `content_type` maydonining default'i `short` — shu bois
        # "if not self.content_type" ishlamaydi va Filmlar/Multfilmlar orqali
        # saqlangan videolar `short` bo'lib qolib ketardi.)
        self.content_type = content_type_value
        Short.save(self, *args, **kwargs)

    cls_name = "".join(w.capitalize() for w in content_type_value.split("_")) + "Video"
    attrs = {
        "__module__": __name__,
        "Meta": Meta,
        "objects": ProxyManager(),
        "save": save_hook,
        "_admin_order": order,
        "_forced_content_type": content_type_value,
    }
    return type(cls_name, (Short,), attrs)


# Foydalanuvchi kartochkasi bo'yicha 4 ta bo'lim.
ShortVideo = _make_short_proxy("short", "1. Shorts", "1. Shorts (qisqa video)", 1)
NewsVideo = _make_short_proxy("news", "2. Yangilik (video)", "2. News videolar", 2)
MovieVideo = _make_short_proxy("movie", "3. Film / uzun video", "3. Filmlar", 3)
CartoonVideo = _make_short_proxy("cartoon", "4. Multfilm", "4. Multfilmlar", 4)

SHORT_PROXIES = [ShortVideo, NewsVideo, MovieVideo, CartoonVideo]


class AllYoutubeVideo(Short):
    """Faqat admin ro'yxati sifatida ishlatiladigan "fake" proxy.

    Admin bo'limi bu proxy uchun MAXSUS `changelist_view` bilan yozilgan:
    u `Short` VA `Dictation` (barcha turlari) modellaridan `youtube_link`
    mavjud bo'lgan barcha yozuvlarni yig'ib ko'rsatadi.

    Foydalanuvchi qidiruv orqali biror videoni topib, o'sha model'ning
    change formasiga o'tadi va tur (`content_type` yoki `type`) ni
    almashtiradi. Modellar orasidagi almashtirish (masalan Dictation'ni
    Short'ga) qo'llab-quvvatlanmaydi — bu ma'lumot yo'qotishga olib keladi.
    """
    class Meta:
        proxy = True
        app_label = "catalog"
        verbose_name = "Umumiy YouTube video"
        verbose_name_plural = "6. Umumiy videolar (Short + Diktant)"


class AIJob(TimeStampedModel):
    """Persistent AI ish navbati — admin saqlagach fon rejimida bajariladi.

    Admin YouTube link'ni kiritib saqlaydi → signal AIJob yaratadi → alohida
    worker (management command yoki Django ishga tushishida ochiladigan thread)
    ketma-ket bajaradi:
      1) Whisper (transkript) — Short yoki Dictation uchun
      2) Haiku (savollar) — transkript tayyor bo'lgach
      3) Ingest (kanaldan ko'p video olib kelish) — ChannelIngestTask uchun

    Server o'chib yonsa DB'da qolgan `pending`/`running` job'lar keyingi ishga
    tushishida qayta olinadi — user'ga hech nima yo'qolmaydi.
    """

    class Kind(models.TextChoices):
        SHORT = "short", "Short"
        DICTATION = "dictation", "Dictation"
        CHANNEL = "channel", "Kanal ingest"
        IELTS = "ielts", "IELTS Listening test"

    class Step(models.TextChoices):
        WHISPER = "whisper", "Whisper (transkript)"
        HAIKU = "haiku", "Haiku (savollar)"
        INGEST = "ingest", "Kanaldan yig'ish"
        PARSE = "parse", "Parser (HTML)"

    class Status(models.TextChoices):
        PENDING = "pending", "Kutayapti"
        RUNNING = "running", "Ishlanmoqda"
        DONE = "done", "Tayyor"
        FAILED = "failed", "Xato"

    kind = models.CharField("Turi", max_length=12, choices=Kind.choices, db_index=True)
    object_id = models.PositiveIntegerField("Obyekt ID", db_index=True)
    step = models.CharField("Bosqich", max_length=10, choices=Step.choices)
    status = models.CharField(
        "Holat", max_length=8, choices=Status.choices,
        default=Status.PENDING, db_index=True,
    )
    error = models.TextField("Xato matni", blank=True, default="")
    attempts = models.PositiveIntegerField("Urinishlar", default=0)
    started_at = models.DateTimeField("Boshlandi", null=True, blank=True)
    finished_at = models.DateTimeField("Tugadi", null=True, blank=True)

    class Meta:
        verbose_name = "AI ish"
        verbose_name_plural = "AI ish navbati"
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["kind", "object_id", "step"]),
        ]
        # Bitta obyekt uchun bitta bosqichda faqat bitta navbat yozuvi bo'ladi.
        constraints = [
            models.UniqueConstraint(
                fields=["kind", "object_id", "step"],
                name="uniq_ai_job_per_step",
            ),
        ]

    def __str__(self):
        return f"[{self.status}] {self.kind}#{self.object_id} {self.step}"


class DeadVideoReport(TimeStampedModel):
    """Foydalanuvchi tomonidan "video mavjud emas" deb belgilangan har bir
    YouTube video haqidagi yozuv. **Har foydalanuvchi hisobga olinadi** — bir
    foydalanuvchi Postman orqali istalgan videoni "o'lik" deb belgilay olmaydi.
    Server o'zi oEmbed orqali tekshiradi va faqat shundan keyin `verified=True`
    yoziladi.

    Manba video ikki xil bo'lishi mumkin — `Short` yoki `Dictation` (ikkalasi
    ham YouTube link ishlatadi). Shu bois ikkala FK ham nullable. Agar Short/
    Dictation modellari o'chirilsa report qolaveradi (`SET_NULL`).

    Admin panelida:
      - `verified=True` = server tasdiqladi, xavfsiz o'chirish mumkin
      - `verified=False` = foydalanuvchi shikoyati bor, lekin server hali
        tasdiqlamadi (yoki tekshirib bo'lmadi)
    Bulk amali orqali `verified=True` bo'lganlarni tanlab ularning MANBA
    videosini butunlay o'chirish mumkin.
    """

    class VerifyResult(models.TextChoices):
        UNKNOWN = "unknown", "Tekshirilmagan"
        ALIVE = "alive", "Tekshirildi — mavjud"
        DEAD = "dead", "Tekshirildi — yo'q"
        ERROR = "error", "Tekshirib bo'lmadi"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="Xabarchi",
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name="dead_video_reports",
    )
    youtube_url = models.URLField("YouTube havolasi")
    youtube_id = models.CharField(
        "YouTube ID", max_length=16, blank=True, default="", db_index=True,
    )
    short = models.ForeignKey(
        "Short", verbose_name="Manba Short",
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name="dead_reports",
    )
    dictation = models.ForeignKey(
        "Dictation", verbose_name="Manba Diktant",
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name="dead_reports",
    )
    verified = models.BooleanField(
        "Server tasdiqladi", default=False, db_index=True,
        help_text="Server oEmbed orqali videoning haqiqatan yo'qligini tasdiqladimi.",
    )
    verify_result = models.CharField(
        "Tekshiruv natijasi", max_length=10, choices=VerifyResult.choices,
        default=VerifyResult.UNKNOWN,
    )
    verify_detail = models.CharField(
        "Tekshiruv izohi", max_length=200, blank=True, default="",
    )

    class Meta:
        verbose_name = "O'lik video xabari"
        verbose_name_plural = "O'lik video xabarlari"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["verified", "-created_at"]),
        ]

    def __str__(self):
        who = self.user or "anon"
        return f"{who} → {self.youtube_id or self.youtube_url} ({self.verify_result})"


class ReactionValue(models.TextChoices):
    LIKE = "like", "Like"
    DISLIKE = "dislike", "Dislike"


class ShortReaction(TimeStampedModel):
    """Foydalanuvchining Short'ga like/dislike'i — HAR USER 1 MARTA.

    Server tomonda cheklanadi (`unique_together`): bir foydalanuvchi bir
    videoga faqat bitta reaksiya qo'yadi (like YOKI dislike), qayta bosish
    o'chiradi yoki almashtiradi. Hisoblagichlar `Short.likes/dislikes` da.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="short_reactions", verbose_name="Foydalanuvchi",
    )
    short = models.ForeignKey(
        "Short", on_delete=models.CASCADE,
        related_name="reactions_by_user", verbose_name="Short",
    )
    value = models.CharField("Reaksiya", max_length=8, choices=ReactionValue.choices)

    class Meta:
        verbose_name = "Short reaksiyasi"
        verbose_name_plural = "Short reaksiyalari"
        unique_together = ("user", "short")

    def __str__(self):
        return f"{self.user} {self.value} {self.short_id}"


class DictationReaction(TimeStampedModel):
    """Diktant/video'ga like/dislike — HAR USER 1 MARTA (Short bilan bir xil)."""
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="dictation_reactions", verbose_name="Foydalanuvchi",
    )
    dictation = models.ForeignKey(
        "Dictation", on_delete=models.CASCADE,
        related_name="reactions_by_user", verbose_name="Diktant",
    )
    value = models.CharField("Reaksiya", max_length=8, choices=ReactionValue.choices)

    class Meta:
        verbose_name = "Diktant reaksiyasi"
        verbose_name_plural = "Diktant reaksiyalari"
        unique_together = ("user", "dictation")

    def __str__(self):
        return f"{self.user} {self.value} {self.dictation_id}"


class ShortReport(TimeStampedModel):
    """Foydalanuvchining Short haqidagi shikoyati (YouTube Shorts "report" kabi).

    Bitta user + bitta short = eng ko'p 1 ta shikoyat. Sabab tanlanadi,
    ixtiyoriy izoh matnida qo'shimcha ma'lumot.
    """

    class Reason(models.TextChoices):
        SEXUAL = "sexual", "Jinsiy kontent"
        VIOLENT = "violent", "Zo'ravonlik / xavfli harakat"
        HATE = "hate", "Nafrat / haqorat"
        HARASSMENT = "harassment", "Ta'qib / bezovtalash"
        MISINFO = "misinfo", "Yolg'on / noto'g'ri ma'lumot"
        SPAM = "spam", "Spam / firibgarlik"
        CHILD_SAFETY = "child_safety", "Bolalar xavfsizligi"
        OTHER = "other", "Boshqa"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="Foydalanuvchi",
        on_delete=models.CASCADE, related_name="short_reports",
    )
    short = models.ForeignKey(
        "Short", verbose_name="Short",
        on_delete=models.CASCADE, related_name="reports",
    )
    reason = models.CharField("Sabab", max_length=20, choices=Reason.choices)
    text = models.TextField(
        "Qo'shimcha izoh", blank=True, default="",
        help_text="Ixtiyoriy — user shikoyatga sharh qo'shishi mumkin.",
    )

    class Meta:
        verbose_name = "Short shikoyati"
        verbose_name_plural = "Short shikoyatlari"
        ordering = ["-created_at"]
        # Bitta user + bitta short = eng ko'p 1 ta shikoyat.
        unique_together = ("user", "short")

    def __str__(self):
        return f"{self.user} → {self.short} ({self.get_reason_display()})"


class ShortQuestionFeedback(TimeStampedModel):
    """Foydalanuvchining Short'dagi savol xato tuzilganligi haqidagi belgisi.

    Bitta user + bitta short = eng ko'p 1 ta feedback. Erkin matn (masalan
    "1-savol xato tuzilgan, javob True bo'ladi").
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="Foydalanuvchi",
        on_delete=models.CASCADE, related_name="short_question_feedbacks",
    )
    short = models.ForeignKey(
        "Short", verbose_name="Short",
        on_delete=models.CASCADE, related_name="question_feedbacks",
    )
    text = models.TextField(
        "Feedback matni",
        help_text="Foydalanuvchi qaysi savol qanday xato ekanligini yozadi.",
    )

    class Meta:
        verbose_name = "Savol xatolik haqida"
        verbose_name_plural = "Savol xatolik xabarlari"
        ordering = ["-created_at"]
        unique_together = ("user", "short")

    def __str__(self):
        return f"{self.user} → {self.short}: {self.text[:60]}"


class DictationReport(TimeStampedModel):
    """Foydalanuvchining diktant/listening test haqidagi shikoyati.

    `ShortReport` bilan bir xil g'oya, lekin `Dictation` uchun — video
    bo'lgan har qanday sahifada (news, movies, cartoons, ...) foydalanuvchi
    kontentdan shikoyat qila oladi. Bitta user + bitta diktant = 1 shikoyat.
    """

    # Sabablar Short bilan bir xil — ikki joyda ikkita ro'yxat saqlamaymiz.
    Reason = ShortReport.Reason

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="Foydalanuvchi",
        on_delete=models.CASCADE, related_name="dictation_reports",
    )
    dictation = models.ForeignKey(
        Dictation, verbose_name="Diktant",
        on_delete=models.CASCADE, related_name="reports",
    )
    reason = models.CharField("Sabab", max_length=20, choices=Reason.choices)
    text = models.TextField(
        "Qo'shimcha izoh", blank=True, default="",
        help_text="Ixtiyoriy — user shikoyatga sharh qo'shishi mumkin.",
    )

    class Meta:
        verbose_name = "Diktant shikoyati"
        verbose_name_plural = "Diktant shikoyatlari"
        ordering = ["-created_at"]
        unique_together = ("user", "dictation")

    def __str__(self):
        return f"{self.user} → {self.dictation} ({self.get_reason_display()})"


class DictationQuestionFeedback(TimeStampedModel):
    """Diktantning AI savoli xato tuzilganligi haqidagi xabar.

    Bitta user + bitta diktant = eng ko'p 1 ta xabar. Erkin matn — masalan
    "3-savolning to'g'ri javobi False bo'lishi kerak".
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="Foydalanuvchi",
        on_delete=models.CASCADE, related_name="dictation_question_feedbacks",
    )
    dictation = models.ForeignKey(
        Dictation, verbose_name="Diktant",
        on_delete=models.CASCADE, related_name="question_feedbacks",
    )
    text = models.TextField(
        "Feedback matni",
        help_text="Foydalanuvchi qaysi savol qanday xato ekanligini yozadi.",
    )

    class Meta:
        verbose_name = "Diktant savol xatoligi"
        verbose_name_plural = "Diktant savol xatoliklari"
        ordering = ["-created_at"]
        unique_together = ("user", "dictation")

    def __str__(self):
        return f"{self.user} → {self.dictation}: {self.text[:60]}"


class ChannelIngestTask(TimeStampedModel):
    """YouTube kanaldan oxirgi N ta videoni tizimga qo'shish uchun task.

    Admin bitta URL + N ni beradi, `content_type` orqali tayinlangan modulga
    (Shorts/News/Cartoons/Movies yoki Random Videos diktantlari) qo'yiladi:
      1) yt-dlp `--flat-playlist` orqali kanalning eng yangi videolarini
         ro'yxatlaydi
      2) Har videoning `youtube_id` ni bazadan qidiradi (Short YOKI Dictation).
         Allaqachon mavjud bo'lsa **SKIP** — bu count'ga hisoblanmaydi;
         eski videoga o'tadi. Shu tarzda `count` ta YANGI video yig'iladi.
      3) Har yangi video Short yoki Dictation sifatida saqlanadi — signal
         AI pipeline'ini avtomatik ishga tushiradi (Whisper + Haiku).
      4) Task `done` ga o'tadi (yoki katalog tugasa `partial` — count'ga
         yetmadi lekin nima olinganini yozib qoldiradi).

    Task ham AIJob navbati orqali bajariladi (kind=CHANNEL, step=INGEST) —
    server o'chib yonsa keyingi ishga tushishida o'zi qayta olinadi.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Kutayapti"
        RUNNING = "running", "Ishlanmoqda"
        DONE = "done", "Tayyor"
        PARTIAL = "partial", "Qisman (katalog tugadi)"
        FAILED = "failed", "Xato"

    class TargetKind(models.TextChoices):
        """Sayt'da video ko'rsatiladigan 5 bo'lim — foydalanuvchi shundan
        birini tanlaydi va URL beradi. Boshqa hech qanday variant kerak emas.

        - `SHORTS`, `MOVIES`, `CARTOONS`, `NEWS` → Short modeliga (content_type)
        - `RANDOM_VIDEOS` → Dictation modeliga (type=random_video)
        """
        SHORTS = "shorts", "Shorts"
        MOVIES = "movies", "Filmlar"
        CARTOONS = "cartoons", "Multfilmlar"
        NEWS = "news", "Yangiliklar"
        RANDOM_VIDEOS = "random_videos", "Tasodifiy videolar"

    target_kind = models.CharField(
        "Qaysi bo'limga qo'yiladi", max_length=16, choices=TargetKind.choices,
        default=TargetKind.SHORTS,
        help_text="Videolar shu bo'limga qo'shiladi. Shorts uchun URL ga "
                  "/shorts qo'shing, oddiy videolar uchun /videos.",
    )
    channel_url = models.URLField(
        "Kanal URL", max_length=500,
        help_text="Masalan: https://www.youtube.com/@WhiteHouse/videos "
                  "yoki https://www.youtube.com/@WhiteHouse/shorts",
    )
    count = models.PositiveSmallIntegerField(
        "Video soni (N)", default=10,
        help_text="Kanalning eng yangi N ta YANGI videosini oladi. Bazada "
                  "allaqachon bo'lganlar tashlab yuboriladi (count'ga hisoblanmaydi) "
                  "— eski videolarga o'tib N tani to'ldiradi.",
    )

    status = models.CharField(
        "Holat", max_length=10, choices=Status.choices,
        default=Status.PENDING, db_index=True,
    )
    error = models.TextField("Xato matni", blank=True, default="")
    videos_created = models.JSONField(
        "Yaratilgan videolar", default=list, blank=True,
        help_text="[{'youtube_id': '...', 'title': '...', 'target_id': 42}, ...]",
    )
    videos_skipped = models.JSONField(
        "Tashlab yuborilgan videolar", default=list, blank=True,
        help_text="Allaqachon bazada mavjud bo'lganlar.",
    )
    started_at = models.DateTimeField("Boshlandi", null=True, blank=True)
    finished_at = models.DateTimeField("Tugadi", null=True, blank=True)

    class Meta:
        verbose_name = "Kanal ingest task"
        verbose_name_plural = "Kanal ingest tasklar"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "-created_at"]),
        ]

    def __str__(self):
        return f"[{self.status}] {self.channel_url} × {self.count} → {self.target_kind}"


class DictationProgress(TimeStampedModel):
    """Foydalanuvchining bitta diktant bo'yicha progressi.

    Yopilgan tab hech qachon progressni yo'qotmasligi uchun — foydalanuvchi
    diktantga qaytsa qayerdan qolganini ko'radi.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="Foydalanuvchi",
        on_delete=models.CASCADE, related_name="dictation_progress",
    )
    dictation = models.ForeignKey(
        Dictation, verbose_name="Diktant",
        on_delete=models.CASCADE, related_name="progress_entries",
    )
    percent = models.PositiveSmallIntegerField("Foiz", default=0)
    last_index = models.PositiveIntegerField("Oxirgi chunk", default=0)
    draft_answers = models.JSONField("Saqlangan javoblar", default=dict, blank=True)
    completed_at = models.DateTimeField("Tugatilgan", null=True, blank=True)

    class Meta:
        verbose_name = "Diktant progressi"
        verbose_name_plural = "Diktant progresslari"
        unique_together = ("user", "dictation")
        ordering = ["-updated_at"]

    def __str__(self):
        return f"{self.user} — {self.dictation.title} ({self.percent}%)"


class IeltsListeningTest(TimeStampedModel):
    """IELTS Listening tayyor testi (engnovate.com kabi manbadan parse qilingan).

    Ish oqimi:
      1. Admin `source_url` beradi va saqlaydi. Signal `AIJob(kind=IELTS, step=PARSE)`
         yaratadi. Worker `ielts_parser.parse_test` ni chaqiradi:
         `html` (audio bilan standalone sahifa) + `total_questions` (odatda 40)
         maydonlari to'ldiriladi, status -> `parsed`.
      2. Admin javoblarni qo'lda kiritadi (1..N). Har savol uchun ro'yxat —
         bir necha to'g'ri javob mumkin (masalan: `["Monday", "monday"]`).
         Barcha savol to'ldirilgach `is_ready=True` avtomatik yoqiladi.
      3. `is_published=True` bo'lsa frontendda ko'rinadi (`/ielts-tests/:slug`).
         Sayt HTML ni sandbox iframe (srcdoc) ichida ko'rsatadi, foydalanuvchi
         topshirgan javoblarni backend `answers` bilan solishtiradi.

    Grading logikasi: har savol javobi ro'yxatdagi biror variantga to'g'ri
    kelsa (normalize: lowercase, tinish/space trim) — to'g'ri.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Kutayapti (parser)"
        PARSING = "parsing", "Parse qilinmoqda"
        PARSED = "parsed", "Parse qilindi (javob kutilmoqda)"
        READY = "ready", "Tayyor"
        FAILED = "failed", "Xato"

    source_url = models.URLField(
        "Manba URL", max_length=500, unique=True,
        help_text="Masalan: https://engnovate.com/... — parser shu sahifadan "
                  "test HTML sini olib chiqadi (audio havolalari bilan).",
    )
    title = models.CharField(
        "Sarlavha (avto)", max_length=250, blank=True, default="",
        help_text="Parser sahifa &lt;title&gt; tegidan oladi. Qo'lda tahrirlash mumkin.",
    )
    slug = models.SlugField("Slug", max_length=280, unique=True, blank=True)

    html = models.TextField(
        "Standalone HTML (parser natijasi)", blank=True, default="",
        help_text="Parser tayyorlagan to'liq sahifa (audio + savollar). "
                  "Sayt iframe srcdoc ichida ko'rsatadi.",
    )
    parts_json = models.JSONField(
        "Partlar (xom)", default=list, blank=True,
        help_text="Parser ajratib olgan partlar: audio havolasi, savollar HTML'i "
                  "va savol raqamlari. HTML shundan yasaladi — shu bois plyer "
                  "yaxshilanganda manba saytga QAYTA MUROJAAT QILMASDAN "
                  "sahifani qayta yasash mumkin (`manage.py rebuild_ielts_html`).",
    )
    total_questions = models.PositiveSmallIntegerField(
        "Savollar soni", default=40,
        help_text="Parser aniqlaydi. Standart IELTS Listening — 40 ta.",
    )
    answers = models.JSONField(
        "To'g'ri javoblar (1..N)", default=dict, blank=True,
        help_text='Har savol uchun ro\'yxat, masalan: '
                  '{"1": ["Monday", "monday"], "2": ["blue"], ...}. '
                  'Ro\'yxatdagi biror element foydalanuvchi javobiga to\'g\'ri '
                  'kelsa — savol to\'g\'ri hisoblanadi (case-insensitive).',
    )

    status = models.CharField(
        "Holat", max_length=10, choices=Status.choices,
        default=Status.PENDING, db_index=True,
    )
    parse_error = models.TextField("Parser xatosi", blank=True, default="")

    is_published = models.BooleanField("Chop etilgan", default=False, db_index=True)
    views = models.PositiveIntegerField("Ko'rishlar", default=0)

    class Meta:
        verbose_name = "IELTS Listening test"
        verbose_name_plural = "IELTS Listening testlar"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["is_published", "-created_at"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return self.title or f"IELTS test #{self.pk}"

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.title) if self.title else ""
            if not base:
                base = f"ielts-test"
            slug = base
            if IeltsListeningTest.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                from django.utils import timezone
                stamp = timezone.localtime().strftime("%Y%m%d-%H%M")
                slug = f"{base}-{stamp}"
                counter = 2
                while IeltsListeningTest.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                    slug = f"{base}-{stamp}-{counter}"
                    counter += 1
            self.slug = slug
        super().save(*args, **kwargs)

    @property
    def answered_count(self) -> int:
        """1..N dan nechtasiga kamida bitta javob yozilgan."""
        if not isinstance(self.answers, dict):
            return 0
        n = 0
        for q in range(1, (self.total_questions or 0) + 1):
            vals = self.answers.get(str(q)) or self.answers.get(q)
            if isinstance(vals, (list, tuple)) and any(str(v).strip() for v in vals):
                n += 1
            elif isinstance(vals, str) and vals.strip():
                n += 1
        return n

    @property
    def is_ready(self) -> bool:
        return (
            self.status == self.Status.PARSED
            or self.status == self.Status.READY
        ) and self.answered_count >= (self.total_questions or 0) and (self.total_questions or 0) > 0


class IeltsListeningTestResult(TimeStampedModel):
    """Foydalanuvchining IELTS testdagi natijasi — HAR (user, test) uchun BITTA
    (oxirgi topshiriq). Qayta topshirsa yangilanadi (`update_or_create`), shu
    bois "oldingi natija" doim oxirgisi bo'ladi va tarix shishmaydi.

    Profil/ro'yxatdagi "bajarilgan" belgisi va test sahifasiga qaytilganda
    ko'rsatiladigan natija shu jadvaldan olinadi. `results_json` — savol-savol
    to'g'ri/xato (`{"1": true, "2": false, ...}`), qayta ochilganda javoblar
    yashil/qizil bo'lib tiklanadi.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="ielts_results", verbose_name="Foydalanuvchi",
    )
    test = models.ForeignKey(
        IeltsListeningTest, on_delete=models.CASCADE,
        related_name="results", verbose_name="Test",
    )
    score = models.PositiveSmallIntegerField("Ball", default=0)
    total = models.PositiveSmallIntegerField("Jami savol", default=0)
    results_json = models.JSONField(
        "Savol natijalari", default=dict, blank=True,
        help_text='{"1": true, "2": false, ...}',
    )

    class Meta:
        verbose_name = "IELTS natija"
        verbose_name_plural = "IELTS natijalar"
        unique_together = ("user", "test")
        ordering = ["-updated_at"]
        indexes = [models.Index(fields=["user", "test"])]

    def __str__(self):
        return f"{self.user_id} · {self.test_id} — {self.score}/{self.total}"

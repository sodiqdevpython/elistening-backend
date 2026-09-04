"""Umumiy Django sozlamalari (dev va prod shu fayldan meros oladi)."""
from datetime import timedelta
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    SECRET_KEY=(str, "dev-only-insecure-key-change-me"),
    ALLOWED_HOSTS=(list, ["*"]),
    DATABASE_URL=(str, ""),
    REDIS_URL=(str, ""),
    CORS_ALLOW_ALL=(bool, True),
    TELEGRAM_BOT_TOKEN=(str, ""),
    TELEGRAM_BOT_USERNAME=(str, "elistening_bot"),
    SITE_URL=(str, "http://192.168.1.178:5173"),
    GPT_API_KEY=(str, ""),
    CLAUDE_API_KEY=(str, ""),
    HCAPTCHA_SECRET=(str, ""),
    HCAPTCHA_SITEKEY=(str, ""),
    # Prod domen(lar)i — CSRF/CORS ro'yxatiga qo'shiladi. Masalan:
    # EXTRA_ORIGINS=https://listening.uz,https://www.listening.uz
    EXTRA_ORIGINS=(list, []),
)
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

# hCaptcha — login (OTP verify) uchun. Secret bo'sh bo'lsa captcha O'CHIQ.
HCAPTCHA_SECRET = env("HCAPTCHA_SECRET")
HCAPTCHA_SITEKEY = env("HCAPTCHA_SITEKEY")

# Doimiy TEST akkauntlar — bu kodlar kiritilsa har doim mos akkauntga kiradi
# (bot kodisiz). Har biri o'z tarifida. Bot generatori bu kodlarni HECH QACHON
# chiqarmaydi (`bot/services.py` RESERVED_CODES). Kirishlar admin'da yoziladi.
#   {kod: (username, tarif_kodi)}
TEST_ACCOUNTS = {
    "789878": ("test_account", "free"),
    "789888": ("test_plus", "plus"),
    "789898": ("test_pro", "pro"),
}
TEST_OTP_CODE = env("TEST_OTP_CODE", default="789878")  # eski kod (free) — moslik uchun

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # 3rd party
    "rest_framework",
    "rest_framework_simplejwt",
    "django_filters",
    "corsheaders",
    "drf_spectacular",
    # local
    "apps.common",
    "apps.accounts",
    "apps.catalog",
    "apps.billing",
    "apps.telegrambot",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# --- Database -------------------------------------------------------------
# DATABASE_URL bo'lmasa sqlite ishlatiladi — admin orqali qo'lda ma'lumot
# kiritish uchun hech qanday qo'shimcha xizmat kerak emas.
# Prod'da PostgreSQL (docker-compose `db` xizmati) — bir nechta yozuvchi
# jarayon (web + celery worker) bir vaqtda ishlaydi, SQLite'ning "database
# is locked" muammosi umuman bo'lmaydi.
if env("DATABASE_URL"):
    DATABASES = {"default": env.db("DATABASE_URL")}
    # Postgres ulanishini biroz uzoqroq ochiq tutamiz (har so'rovda qayta
    # ulanmaslik uchun) — gunicorn/celery ostida sezilarli tezlik.
    DATABASES["default"].setdefault("CONN_MAX_AGE", 60)
else:
    # SQLite dev: worker THREAD + web bir vaqtda yozadi. `timeout` (busy_timeout)
    # bo'lsa "database is locked" o'rniga 30s kutadi; WAL rejimi esa o'qish va
    # yozishni parallel qiladi (WAL `apps/common/apps.py` da `connection_created`
    # signalida yoqiladi). Bu ikkisi dev'dagi lock xatolarini deyarli yo'q qiladi.
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
            "OPTIONS": {"timeout": 30},
        }
    }

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "uz"
TIME_ZONE = "Asia/Tashkent"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Cache ----------------------------------------------------------------
if env("REDIS_URL"):
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": env("REDIS_URL"),
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "listening-uz",
        }
    }

# --- DRF ------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        # Sessiyaga bog'liq JWT — bir platformada bitta qurilma (auth.py).
        "apps.accounts.auth.SessionJWTAuthentication",
        # CSRF-exempt session auth — SPA JWT bearer ishlatadi. Oddiy
        # SessionAuthentication brauzerda admin session cookie bo'lsa har
        # POST'ga CSRF token majburlab, login'ni "CSRF Failed" bilan
        # bloklardi. API stateless (bearer) — CSRF kerak emas.
        "apps.common.authentication.CsrfExemptSessionAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.AllowAny",),
    "DEFAULT_PAGINATION_CLASS": "apps.common.pagination.StandardPageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_THROTTLE_CLASSES": (
        "rest_framework.throttling.ScopedRateThrottle",
    ),
    "DEFAULT_THROTTLE_RATES": {
        # 6 xonali kod 1 daqiqa yashaydi — brute-force ni qattiq cheklaymiz.
        "otp_verify": "5/min",
    },
    "EXCEPTION_HANDLER": "apps.common.exceptions.api_exception_handler",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "eListening.uz API",
    "DESCRIPTION": "Ingliz tili tinglab tushunish platformasi API",
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(days=1),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=30),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": False,
    "UPDATE_LAST_LOGIN": True,
}

CORS_ALLOW_ALL_ORIGINS = env("CORS_ALLOW_ALL")
# Prod domen(lar)i `EXTRA_ORIGINS` env orqali qo'shiladi (masalan
# https://listening.uz) — admin/login HTTPS'da CSRF xatosiz ishlashi uchun SHART.
_BASE_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173", "http://192.168.1.178:5173", "https://sodiqdevpython.jprq.live"]
CORS_ALLOWED_ORIGINS = _BASE_ORIGINS + env("EXTRA_ORIGINS")
CSRF_TRUSTED_ORIGINS = _BASE_ORIGINS + env("EXTRA_ORIGINS")

# --- Celery ---------------------------------------------------------------
# Celery hozircha ishlatilmaydi (AI ingest o'chirilgani sabab), lekin
# sozlama qoladi — kelajakda BotMessage yuborish yoki OpenAI ingest
# uchun kerak bo'lishi mumkin.
# --- Celery ---------------------------------------------------------------
# `USE_CELERY` — REDIS_URL berilgan bo'lsa (prod/docker) AI ishlari CELERY
# orqali orqa fonda bajariladi. Bo'lmasa (lokal SQLite dev) — `apps/catalog`
# ichidagi THREAD worker (poll) ishlaydi va celery umuman ishlatilmaydi.
#
# MUHIM (VPS o'chib-yonsa davom etish): navbat manbasi — DB'dagi `AIJob`
# jadvali (durable). Signal AIJob yaratadi + celery task jo'natadi. Agar
# xabar yo'qolsa yoki server qayta ishga tushsa, `sweep_pending_jobs` beat
# taski har 20s da `pending`/`stale` job'larni topib qayta jo'natadi — hech
# nima yo'qolmaydi.
USE_CELERY = bool(env("REDIS_URL"))
CELERY_BROKER_URL = env("REDIS_URL") or "memory://"
CELERY_RESULT_BACKEND = env("REDIS_URL") or "cache+memory://"
CELERY_TASK_ALWAYS_EAGER = not USE_CELERY
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_TASK_DEFAULT_QUEUE = "default"
# Worker o'ldirilsa (VPS restart) ish qayta yetkaziladi — yo'qolmaydi.
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True

# Og'ir kanal-ingest ishi alohida `ingest` navbatiga `dispatch_job` orqali
# yo'naltiriladi (worker-ingest uni iste'mol qiladi) — foydalanuvchi
# vazifalarini (bitta short/dictation) to'sib qo'ymasin.

# Beat: durable navbatni muntazam supuradi (crash-recovery + kechikkan ishlar).
CELERY_BEAT_SCHEDULE = {
    "sweep-pending-ai-jobs": {
        "task": "apps.catalog.tasks.sweep_pending_jobs",
        "schedule": 20.0,   # har 20 soniyada
    },
}

# --- Loyiha sozlamalari ---------------------------------------------------
TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN")
TELEGRAM_BOT_USERNAME = env("TELEGRAM_BOT_USERNAME")
SITE_URL = env("SITE_URL")

# --- Kontent himoyasi (obfuskatsiya) --------------------------------------
# Transkript va savollar javobda "o'ralgan" holda ketadi (`apps/common/protect.py`).
# Bu SHIFRLASH EMAS: kalit mijozga baribir yetadi. Maqsad - `curl` bilan
# JSON'ni olib ketishni ma'nosiz qilish. Mijozlarda AYNAN shu parol turishi
# shart: frontend `VITE_CONTENT_SECRET`, mobil `EXPO_PUBLIC_CONTENT_SECRET`.
# Testlar: har testdan oldin kesh tozalanadi. Django bazani rollback qiladi,
# lekin KESHGA tegmaydi — kalitlar esa `user.pk` ga bog'langan, pk'lar esa
# har testda 1 dan boshlanadi. Natijada bir test boshqasining tarifini/
# throttle hisobini ko'rib, YOLG'ON yiqilardi. Batafsil: `apps/common/testing.py`.
TEST_RUNNER = "apps.common.testing.CacheIsolatedRunner"

CONTENT_SECRET = env("CONTENT_SECRET", default="sodiq2005.py")
PROTECT_CONTENT = env.bool("PROTECT_CONTENT", default=True)

# AI transkripsiya (OpenAI Whisper)
GPT_API_KEY = env("GPT_API_KEY")
CLAUDE_API_KEY = env("CLAUDE_API_KEY")   # kelajakda Haiku uchun

OTP_TTL_SECONDS = 60
OTP_CODE_LENGTH = 6

"""Diktant admin — bitta bo'lim, sodda va aniq.

Har diktant uchun:
- Sarlavha, mavzu (type), daraja, audio yuklash
- `is_media` → belgilansa `youtube_link` ochiladi
- `body` JSON — timestamp bilan matn
- **🎬 Segment editor** — waveform ustida qo'lda gap belgilash + matn kiritish
- **🤖 AI to'ldirish** — OpenAI Whisper orqali avtomatik transkript
  (button change_form.html template'ida — CSRF tokeni orqali ishlaydi)

Ro'yxatda change_list `Dictation.light` manager'idan foydalanadi —
`body` / `full_text` / `words_json` yuklanmaydi (yengil so'rov).
"""
from django import forms
from django.contrib import admin, messages
from django.http import HttpResponseNotAllowed, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import path, reverse
from django.utils.html import format_html

from apps.common.admin import BaseModelAdmin

from . import views_admin
from .models import (
    AIJob, AllYoutubeVideo, ChannelIngestTask, DICTATION_PROXIES, DeadVideoReport,
    Dictation, DictationProgress, DictationReaction,
    IeltsListeningTest, SHORT_PROXIES, Short, ShortReaction,
    ShortQuestionFeedback, ShortReport,
    DictationQuestionFeedback, DictationReport,
)


class DictationAdmin(BaseModelAdmin):
    list_display = ("title", "type", "cefr_level", "is_media_badge",
                    "duration_col", "ai_status_badge",
                    "is_published", "segment_editor_link", "created_at")
    list_display_links = ("title",)
    list_filter = ("type", "cefr_level", "is_published", "is_media",
                   "transcription_status", "created_at")
    search_fields = ("title", "slug")
    date_hierarchy = "created_at"
    ordering = ("type", "-created_at")
    prepopulated_fields = {"slug": ("title",)}
    readonly_fields = ("views", "practiced_time", "audio_duration_sec",
                       "transcription_status", "transcription_error",
                       "tests_status", "tests_error",
                       "mcq_questions", "tfng_questions", "fill_gap_questions",
                       "created_at", "updated_at")
    actions = (
        "action_publish", "action_unpublish",
        "action_ai_transcribe", "action_generate_tests",
    )
    # AI to'ldirish tugmasi shu template'ning `object-tools-items` blokida.
    change_form_template = "admin/catalog/dictation/change_form.html"

    fieldsets = (
        (None, {"fields": ("title", "slug", "type", "cefr_level", "is_published")}),
        ("Audio", {
            "fields": ("audio", "audio_duration_sec"),
            "description": "mp3 / wav yuklang. Saqlagach yuqorida 🤖 AI to'ldirish tugmasi paydo bo'ladi.",
        }),
        ("AI transkripsiya holati", {
            "fields": ("transcription_status", "transcription_error"),
        }),
        ("Video (ixtiyoriy)", {
            "fields": ("is_media", "youtube_link"),
            "description": "Diktantda video ham ko'rsatilsin desangiz belgilang.",
        }),
        ("Transkript (timestamp bilan)", {
            "fields": ("body", "full_text", "words_json"),
            "classes": ("collapse",),
            "description": "AI to'ldirsa avtomatik yoziladi. 🎬 Segment editor bilan qo'lda ham tahrirlash mumkin.",
        }),
        ("Listening test (AI)", {
            "fields": ("tests_status", "tests_error",
                       "mcq_questions", "tfng_questions", "fill_gap_questions"),
            "classes": ("collapse",),
            "description": "Actions'dagi '🎯 Listening test yaratish' bulk amali "
                           "yoki har diktantda transkript tayyor bo'lgach chaqiring.",
        }),
        ("Statistika", {"fields": ("views", "practiced_time"),
                        "classes": ("collapse",)}),
        ("Sanalar", {"fields": ("created_at", "updated_at"),
                     "classes": ("collapse",)}),
    )

    # Ro'yxat uchun yengil manager — body/full_text/words_json yuklanmaydi.
    def get_queryset(self, request):
        return Dictation.light.all()

    def get_object(self, request, object_id, from_field=None):
        """Change form uchun to'liq obyekt (body/full_text/words_json bilan)."""
        try:
            return Dictation.objects.get(pk=object_id)
        except Dictation.DoesNotExist:
            return None

    def get_urls(self):
        model_name = self.model._meta.model_name
        my_urls = [
            path(
                "<int:dictation_id>/segment-editor/",
                self.admin_site.admin_view(views_admin.segment_editor),
                name=f"catalog_{model_name}_segment_editor",
            ),
            path(
                "<int:dictation_id>/segment-editor/save/",
                self.admin_site.admin_view(views_admin.segment_editor_save),
                name=f"catalog_{model_name}_segment_editor_save",
            ),
            path(
                "<int:dictation_id>/ai-transcribe/",
                self.admin_site.admin_view(self.ai_transcribe_view),
                name=f"catalog_{model_name}_ai_transcribe",
            ),
        ]
        return my_urls + super().get_urls()

    # --- AI to'ldirish view ------------------------------------------------
    # `@require_POST` bilan bog'lash bound-method bilan ishlamaydi
    # (decorator `func.__wrapped__.method` chaqiradi) — shu bois qo'lda tekshiramiz.
    def ai_transcribe_view(self, request, dictation_id: int):
        """AI to'ldirishni ORQA FONGA navbatga qo'yadi va DARROV qaytaradi.

        Ilgari bu yerda `transcribe_dictation` SINXRON ishlardi — yt-dlp yuklab
        olish + Whisper 1-2 daqiqa davom etib, admin sahifasi shuncha vaqt
        "yuklanib" turardi. Endi faqat AIJob navbatga qo'yiladi (worker/thread
        bajaradi), sahifa darrov qaytadi. Holat yashil bannerda (processing →
        done/failed) ko'rinadi — refresh qilib kuzatiladi.
        """
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])

        dictation = get_object_or_404(Dictation, pk=dictation_id)
        redirect_url = request.META.get("HTTP_REFERER") or reverse(
            "admin:catalog_dictation_change", args=[dictation.pk]
        )

        from .ai_worker import dispatch_job, enqueue_haiku, enqueue_whisper
        from .models import AIJob

        if dictation.transcription_status == Dictation.TranscriptionStatus.DONE:
            # Transkript bor — faqat savollar bosqichini qo'yamiz.
            job, _ = enqueue_haiku(AIJob.Kind.DICTATION, dictation.pk)
            dispatch_job(job)
            msg = "🎯 Listening test savollari orqa fonda tayyorlanmoqda."
        else:
            # To'liq pipeline: whisper (keyin worker o'zi haiku'ni ulaydi).
            job, _ = enqueue_whisper(AIJob.Kind.DICTATION, dictation.pk)
            dispatch_job(job)
            msg = ("🤖 AI to'ldirish orqa fonga qo'yildi (transkript + savollar). "
                   "Bir necha soniya/daqiqada tayyor bo'ladi — sahifani yangilab "
                   "holatni kuzating.")

        self.message_user(request, msg, level=messages.SUCCESS)
        return HttpResponseRedirect(redirect_url)

    # --- Change form context — template AI tugmasi uchun ma'lumot beradi
    def change_view(self, request, object_id, form_url="", extra_context=None):
        extra_context = extra_context or {}
        try:
            obj = Dictation.objects.get(pk=object_id)
        except (Dictation.DoesNotExist, ValueError):
            obj = None
        model_name = self.model._meta.model_name
        if obj:
            extra_context.update({
                "dictation_obj": obj,
                "ai_transcribe_url": reverse(
                    f"admin:catalog_{model_name}_ai_transcribe", args=[obj.pk],
                ),
                "segment_editor_url": reverse(
                    f"admin:catalog_{model_name}_segment_editor", args=[obj.pk],
                ),
            })
        return super().change_view(request, object_id, form_url, extra_context)

    def add_view(self, request, form_url="", extra_context=None):
        """Add formada AI banner + "Save & AI transcribe" tugmasi ko'rsatamiz."""
        extra_context = extra_context or {}
        extra_context.update({
            "dictation_obj": None,
            "show_ai_hint": True,
        })
        return super().add_view(request, form_url, extra_context)

    def response_add(self, request, obj, post_url_continue=None):
        """`Save & AI transcribe` tugmasi bosilganda darrov ORQA FONGA qo'yamiz
        va change page'ga o'tamiz — admin sinxron kutmaydi (ilgari 1-2 daqiqa
        bloklanardi). Signal ham AIJob yaratadi, lekin bu yerda ham qo'shib
        `dispatch_job` chaqiramiz (idempotent — dublikat bo'lmaydi)."""
        if "_save_ai" in request.POST and obj.pk and (obj.audio or obj.youtube_link):
            from .ai_worker import dispatch_job, enqueue_whisper
            from .models import AIJob
            try:
                job, _ = enqueue_whisper(AIJob.Kind.DICTATION, obj.pk)
                dispatch_job(job)
                self.message_user(
                    request,
                    "🤖 AI to'ldirish orqa fonga qo'yildi. Sahifani yangilab holatni kuzating.",
                    level=messages.SUCCESS,
                )
            except Exception as exc:
                self.message_user(request, f"🤖 Xato: {exc}", level=messages.ERROR)
            model_name = self.model._meta.model_name
            return HttpResponseRedirect(
                reverse(f"admin:catalog_{model_name}_change", args=[obj.pk])
            )
        return super().response_add(request, obj, post_url_continue)

    # --- Row action: segment editor havolasi ------------------------------
    @admin.display(description="Editor")
    def segment_editor_link(self, obj):
        if not obj.audio:
            return format_html('<span style="color:#94a3b8">audio yo\'q</span>')
        model_name = self.model._meta.model_name
        url = reverse(f"admin:catalog_{model_name}_segment_editor", args=[obj.pk])
        return format_html(
            '<a href="{}" class="button" style="background:#8B5CF6;color:#FFF;'
            'padding:4px 10px;border-radius:6px;text-decoration:none;font-size:12px;'
            'font-weight:700">🎬 Editor</a>',
            url,
        )

    @admin.display(description="AI holati", ordering="transcription_status")
    def ai_status_badge(self, obj):
        colors = {
            "idle": ("#94A3B8", "—"),
            "processing": ("#F59E0B", "⏳ Ishlanmoqda"),
            "done": ("#059669", "✓ Tayyor"),
            "failed": ("#EF4444", "⚠ Xato"),
        }
        color, label = colors.get(obj.transcription_status, ("#94A3B8", "—"))
        title = obj.transcription_error[:200] if obj.transcription_status == "failed" else ""
        return format_html(
            '<span style="color:{};font-weight:700" title="{}">{}</span>',
            color, title, label,
        )

    @admin.display(description="Media", ordering="is_media")
    def is_media_badge(self, obj):
        if obj.is_media:
            return format_html('<span style="color:#8B5CF6;font-weight:700">📹 Video</span>')
        return format_html('<span style="color:#64748B">🎧 Audio</span>')

    @admin.display(description="Davomiylik")
    def duration_col(self, obj):
        s = obj.audio_duration_sec or 0
        if not s:
            return "—"
        return f"{s // 60}:{s % 60:02d}"

    # --- Bulk amallar ------------------------------------------------------
    @admin.action(description="Tanlanganlarni chop etish")
    def action_publish(self, request, queryset):
        count = queryset.update(is_published=True)
        self.message_user(request, f"{count} ta diktant chop etildi.")

    @admin.action(description="Chop etishni bekor qilish")
    def action_unpublish(self, request, queryset):
        count = queryset.update(is_published=False)
        self.message_user(request, f"{count} ta diktant yashirildi.")

    @admin.action(description="🤖 Tanlanganlar uchun AI transkript (Whisper) — orqa fonda")
    def action_ai_transcribe(self, request, queryset):
        """Bir necha diktantni AI navbatga qo'yadi (ORQA FONDA). Sinxron
        emas — admin kutmaydi, worker/thread bajaradi."""
        from .ai_worker import dispatch_job, enqueue_whisper
        from .models import AIJob

        ok, failed = 0, 0
        for d in queryset:
            try:
                job, _ = enqueue_whisper(AIJob.Kind.DICTATION, d.pk)
                dispatch_job(job)
                ok += 1
            except Exception as exc:
                failed += 1
                self.message_user(
                    request, f"⚠ {d.title}: {exc}", level=messages.ERROR,
                )
        if ok:
            self.message_user(
                request, f"🤖 {ok} ta diktant AI navbatga qo'yildi (orqa fonda tayyorlanmoqda).",
                level=messages.SUCCESS,
            )
        if not ok and not failed:
            self.message_user(request, "Hech narsa qilinmadi.", level=messages.WARNING)

    @admin.action(description="🎯 Listening test yaratish (Claude — orqa fonda)")
    def action_generate_tests(self, request, queryset):
        """Tanlangan diktantlar uchun Haiku savol generatsiyasini ORQA FONGA
        qo'yadi. Ilgari sinxron edi — har diktant 5-15s Claude'ni kutardi va
        admin ekran bloklanardi. Endi darrov qaytadi."""
        from .ai_worker import dispatch_job, enqueue_haiku
        from .models import AIJob
        ok = 0
        for d in queryset:
            try:
                job, _ = enqueue_haiku(AIJob.Kind.DICTATION, d.pk)
                dispatch_job(job)
                ok += 1
            except Exception as exc:
                self.message_user(
                    request, f"⚠ {d.title}: {exc}", level=messages.ERROR,
                )
        if ok:
            self.message_user(
                request,
                f"🎯 {ok} ta diktant AI navbatga qo'yildi (savollar orqa fonda tayyorlanadi).",
                level=messages.SUCCESS,
            )


# --- Har mavzu uchun alohida admin bo'limi -------------------------------
# Bazaviy `Dictation` admin menuda ko'rinmaydi (has_module_permission=False).
# Uning o'rniga 9 ta proxy: Short Stories, Conversations, TOEIC, IELTS,
# Random Videos, News, TOEFL, Numbers, Spelling Names.


def _fieldsets_for(kind: str):
    """`kind` — 'audio' yoki 'video'. Formani mos ravishda tuzadi."""
    if kind == "video":
        source_block = ("Video (YouTube)", {
            "fields": ("youtube_link",),
            "description": "YouTube havolasini yozing (masalan: "
                           "https://youtu.be/dQw4w9WgXcQ). Sarlavha bo'sh qoldirilsa "
                           "video sarlavhasi avtomatik olinadi. AI to'ldirish yt-dlp "
                           "bilan audioni vaqtinchalik yuklab oladi va o'chiradi.",
        })
    else:
        source_block = ("Audio", {
            "fields": ("audio", "audio_duration_sec"),
            "description": "mp3 / wav yuklang. Saqlagach yuqorida "
                           "🤖 AI to'ldirish va 🎬 Segment editor tugmalari paydo bo'ladi.",
        })
    return (
        (None, {"fields": ("title", "slug", "cefr_level", "is_published")}),
        source_block,
        ("AI transkripsiya holati", {
            "fields": ("transcription_status", "transcription_error"),
        }),
        ("Transkript (timestamp bilan)", {
            "fields": ("body", "full_text", "words_json"),
            "classes": ("collapse",),
            "description": "AI to'ldirsa avtomatik yoziladi. 🎬 Segment editor bilan "
                           "qo'lda ham tahrirlash mumkin.",
        }),
        ("Statistika", {"fields": ("views", "practiced_time"),
                        "classes": ("collapse",)}),
        ("Sanalar", {"fields": ("created_at", "updated_at"),
                     "classes": ("collapse",)}),
    )


def _make_proxy_admin(proxy_cls):
    """Har proxy uchun DictationAdmin'dan meros oluvchi admin class."""
    kind = getattr(proxy_cls, "_admin_media_kind", "audio")
    is_video = kind == "video"
    fs = _fieldsets_for(kind)

    class ProxyAdmin(DictationAdmin):
        # Video-asosli bo'lsa slug generatsiyasi kerak — barcha maydonlar bor
        fieldsets = fs
        # Menuda alohida bo'lim sifatida ko'rinsin
        list_display = ("title", "cefr_level", "duration_col", "ai_status_badge",
                        "is_published", "segment_editor_link", "created_at")

        def get_queryset(self, request):
            # Proxy manager avtomatik type filtri qo'yadi
            return proxy_cls.objects.all().defer("body", "full_text", "words_json")

        def get_object(self, request, object_id, from_field=None):
            try:
                return proxy_cls.objects.get(pk=object_id)
            except proxy_cls.DoesNotExist:
                return None

        def get_form(self, request, obj=None, **kwargs):
            form = super().get_form(request, obj, **kwargs)
            if is_video and "title" in form.base_fields:
                # Video-asosli diktantda title bo'sh qoldirish mumkin —
                # YouTube video sarlavhasi save_model'da avtomatik olinadi.
                form.base_fields["title"].required = False
                form.base_fields["title"].help_text = (
                    "Bo'sh qoldirilsa YouTube video sarlavhasi olinadi."
                )
                if "slug" in form.base_fields:
                    form.base_fields["slug"].required = False
            # CEFR daraja hamma proxy'da ixtiyoriy — AI transkriptdan keyin
            # matn tahliliga qarab avtomatik to'ldiriladi.
            if "cefr_level" in form.base_fields:
                form.base_fields["cefr_level"].required = False
                form.base_fields["cefr_level"].help_text = (
                    "Bo'sh qoldirilsa AI transkriptdan keyin lug'at va gap "
                    "murakkabligiga qarab avtomatik aniqlaydi. Qo'lda o'zgartirish mumkin."
                )
            return form

        def save_model(self, request, obj, form, change):
            # Media rejim: video-asosli bo'lsa is_media=True avtomatik.
            if is_video:
                obj.is_media = True
                # Sarlavha bo'sh va YouTube link bor — video sarlavhasini olib qo'yamiz.
                if not (obj.title or "").strip() and obj.youtube_link:
                    from .transcribe import fetch_youtube_title
                    title = fetch_youtube_title(obj.youtube_link)
                    if title:
                        obj.title = title[:250]
                        self.message_user(
                            request,
                            f"📹 YouTube sarlavha olindi: {obj.title}",
                            level=messages.INFO,
                        )
                    else:
                        obj.title = "YouTube video"
                        self.message_user(
                            request,
                            "⚠ YouTube sarlavhasini olib bo'lmadi. Sarlavhani qo'lda o'zgartiring.",
                            level=messages.WARNING,
                        )
            elif not change:
                # Audio-asosli bo'lsa yangi obyektda is_media=False
                obj.is_media = False
            super().save_model(request, obj, form, change)

    ProxyAdmin.__name__ = f"{proxy_cls.__name__}Admin"
    return ProxyAdmin


# Har proxy modelni admin ga ro'yxatga olamiz
for _proxy in DICTATION_PROXIES:
    admin.site.register(_proxy, _make_proxy_admin(_proxy))


# Bazaviy `Dictation` — menudan yashirilgan (faqat autocomplete uchun kerak).
@admin.register(Dictation)
class _HiddenDictationAdmin(DictationAdmin):
    """Menuda ko'rinmaydi — faqat autocomplete_fields va progress admin uchun.

    Yangi diktant qo'shish uchun 9 ta proxy bo'limlaridan birini oching.
    """

    def get_model_perms(self, request):
        return {}  # Chap navbardan yashiramiz


@admin.register(DictationProgress)
class DictationProgressAdmin(BaseModelAdmin):
    list_display = ("user", "dictation", "percent", "last_index", "completed_at", "updated_at")
    list_filter = ("completed_at", "updated_at")
    search_fields = ("user__username", "user__display_name", "dictation__title")
    autocomplete_fields = ("user", "dictation")
    list_select_related = ("user", "dictation")


# --- Shorts admin --------------------------------------------------------
@admin.register(Short)
class ShortAdmin(BaseModelAdmin):
    """YouTube Shorts admin — foydalanuvchi faqat URL'ni beradi, qolgani AI orqali.

    Ish oqimi:
      1. `/admin/catalog/short/add/` — YouTube URL'ni yozing, saqlang.
      2. Change formada yashil banner — "🤖 AI to'ldirish" tugmasi.
      3. Whisper + Claude Haiku 30-90 s ichida transkript, teglar, CEFR,
         2 MCQ va 2 TFNG (True/False/Not given) savol yaratadi.
      4. Xato bo'lsa qizil bannerda aniq matn.
    """

    list_display = (
        "title_col", "content_type", "is_vertical", "youtube_id", "cefr_range", "ai_status_badge",
        "duration_col", "priority", "views", "is_published", "dead_badge", "created_at",
    )
    # `content_type` va `priority` ro'yxatning o'zida tahrirlanadi — admin
    # video turini (shorts/news/movie/cartoon) qidiruv orqali tez almashtirsa
    # bo'ladi: masalan multfilmga tushib qolgan filmni "movie" ga o'zgartiradi.
    list_editable = ("content_type", "priority")
    list_filter = (
        "content_type", "is_vertical", "is_published", "is_dead",
        "priority", "transcription_status", "created_at",
    )
    search_fields = ("title", "youtube_id", "youtube_link", "full_text")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    readonly_fields = (
        "youtube_id", "title", "duration_sec",
        "full_text", "words_json", "cefr_from", "cefr_to", "tags",
        "mcq_questions", "tfng_questions", "fill_gap_questions",
        "transcription_status", "transcription_error",
        "views", "dead_reported_at", "dead_report_count",
        "created_at", "updated_at",
    )
    actions = (
        "action_publish", "action_unpublish", "action_ai_generate",
        "action_purge_dead",
    )
    change_form_template = "admin/catalog/short/change_form.html"

    fieldsets = (
        (None, {
            "fields": ("content_type", "youtube_link", "priority", "is_published", "is_dead"),
            "description": "Kontent turini tanlang (short/news/cartoon/movie), "
                           "YouTube URL kiriting. Saqlagach yuqorida 🤖 AI to'ldirish "
                           "tugmasi paydo bo'ladi. Savol soni video davomiyligiga "
                           "qarab avtomatik (short'da fill-gap yo'q, boshqalarda "
                           "har 30 s uchun 1 MCQ + 1 TFNG + 1 FillGap). "
                           "`is_dead` — YouTube'da yo'q video (front avtomatik "
                           "yoqadi); Actions'dan 'O'lik'larni butunlay o'chirish' "
                           "bilan tozalash mumkin. "
                           "`priority` — lentada oldinroq chiqarish (0 = oddiy, "
                           "katta raqam = oldinroq). Foydalanuvchi ko'rmagan "
                           "yuqori priority'li video lentaning boshida chiqadi. "
                           "Player shakli HAVOLADAN aniqlanadi: /shorts/ havolasi "
                           "— tik (9:16), oddiy watch?v= havolasi — keng (16:9).",
        }),
        ("O'lik holat (auto)", {
            "fields": ("dead_reported_at", "dead_report_count"),
            "classes": ("collapse",),
        }),
        ("AI holati", {
            "fields": ("transcription_status", "transcription_error"),
        }),
        ("Meta (avto)", {
            "fields": ("youtube_id", "title", "duration_sec"),
            "classes": ("collapse",),
        }),
        ("AI natijasi (avto)", {
            "fields": ("cefr_from", "cefr_to", "tags",
                       "mcq_questions", "tfng_questions", "fill_gap_questions",
                       "full_text", "words_json"),
            "classes": ("collapse",),
        }),
        ("Statistika", {"fields": ("views",), "classes": ("collapse",)}),
        ("Sanalar", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )

    def get_urls(self):
        model_name = self.model._meta.model_name
        my = [
            path(
                "<int:short_id>/ai-generate/",
                self.admin_site.admin_view(self.ai_generate_view),
                name=f"catalog_{model_name}_ai_generate",
            ),
        ]
        return my + super().get_urls()

    def ai_generate_view(self, request, short_id: int):
        """Short AI to'ldirishni ORQA FONGA qo'yadi va darrov qaytadi.
        Ilgari sinxron edi — yt-dlp + Whisper + Claude 30-90s davom etib
        admin ekran shu vaqt bloklanardi."""
        from .ai_worker import dispatch_job, enqueue_whisper
        from .models import AIJob

        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])

        short = get_object_or_404(Short, pk=short_id)
        back = request.META.get("HTTP_REFERER") or reverse(
            "admin:catalog_short_change", args=[short.pk]
        )
        try:
            job, _ = enqueue_whisper(AIJob.Kind.SHORT, short.pk)
            dispatch_job(job)
        except Exception as exc:
            self.message_user(request, f"🤖 Xato: {exc}", level=messages.ERROR)
            return HttpResponseRedirect(back)

        self.message_user(
            request,
            "🤖 AI to'ldirish orqa fonga qo'yildi (transkript + savollar). "
            "Sahifani yangilab holatni kuzating.",
            level=messages.SUCCESS,
        )
        return HttpResponseRedirect(back)

    def change_view(self, request, object_id, form_url="", extra_context=None):
        extra_context = extra_context or {}
        try:
            obj = Short.objects.get(pk=object_id)
        except (Short.DoesNotExist, ValueError):
            obj = None
        model_name = self.model._meta.model_name
        if obj:
            extra_context.update({
                "short_obj": obj,
                "ai_generate_url": reverse(
                    f"admin:catalog_{model_name}_ai_generate", args=[obj.pk],
                ),
            })
        return super().change_view(request, object_id, form_url, extra_context)

    def save_model(self, request, obj, form, change):
        # URL yangi bo'lsa youtube_id ni oldindan chiqarib qo'yamiz.
        from . import mock_data
        yt = mock_data.extract_youtube_id(obj.youtube_link)
        if yt and not obj.youtube_id:
            obj.youtube_id = yt
        super().save_model(request, obj, form, change)

    # --- list ustunlari ---
    @admin.display(description="Sarlavha", ordering="title")
    def title_col(self, obj):
        return obj.title or format_html(
            '<span style="color:#94a3b8">AI to\'ldirmagan</span>'
        )

    @admin.display(description="CEFR")
    def cefr_range(self, obj):
        if obj.cefr_from and obj.cefr_to:
            return f"{obj.cefr_from}–{obj.cefr_to}"
        return "—"

    @admin.display(description="Davomiylik")
    def duration_col(self, obj):
        s = obj.duration_sec or 0
        return f"{s // 60}:{s % 60:02d}" if s else "—"

    @admin.display(description="AI holati", ordering="transcription_status")
    def ai_status_badge(self, obj):
        colors = {
            "idle": ("#94A3B8", "—"),
            "processing": ("#F59E0B", "⏳"),
            "done": ("#059669", "✓ Tayyor"),
            "failed": ("#EF4444", "⚠ Xato"),
        }
        color, label = colors.get(obj.transcription_status, ("#94A3B8", "—"))
        title = obj.transcription_error[:200] if obj.transcription_status == "failed" else ""
        return format_html(
            '<span style="color:{};font-weight:700" title="{}">{}</span>',
            color, title, label,
        )

    @admin.display(description="O'lik", ordering="is_dead")
    def dead_badge(self, obj):
        if not obj.is_dead:
            return "—"
        return format_html(
            '<span style="color:#B91C1C;font-weight:800" '
            'title="{} marta xabar berilgan">☠ O\'lik</span>',
            obj.dead_report_count,
        )

    # --- bulk amallar ---
    @admin.action(description="Chop etish")
    def action_publish(self, request, queryset):
        c = queryset.update(is_published=True)
        self.message_user(request, f"{c} ta Short chop etildi.")

    @admin.action(description="☠ O'lik videolarni BUTUNLAY o'chirish (qaytmas)")
    def action_purge_dead(self, request, queryset):
        """Tanlangan Shorts orasidan `is_dead=True` bo'lganlarini butunlay
        o'chiradi. Barcha matn, timestamp, savollar, reactions, feedbacklar
        cascade orqali ketadi — hech narsa qolmaydi.

        Bexosdan ochiq videoni o'chirmaslik uchun faqat `is_dead=True` bo'lganlarga
        tegadi. Boshqalari ignored (agar kerak bo'lsa alohida `Delete selected`
        amali bor).
        """
        dead_qs = queryset.filter(is_dead=True)
        count = dead_qs.count()
        if count == 0:
            self.message_user(
                request, "Tanlangan Shorts orasida o'lik yo'q. Foydalanuvchilar "
                "hali birortasini o'lik deb belgilamagan.",
                level=messages.WARNING,
            )
            return
        # Cascade delete — related ShortReport / ShortQuestionFeedback ham ketadi.
        deleted, _detail = dead_qs.delete()
        self.message_user(
            request,
            f"☠ {count} ta o'lik Short (jami {deleted} yozuv) butunlay o'chirildi.",
            level=messages.SUCCESS,
        )

    @admin.action(description="Chop etishni bekor qilish")
    def action_unpublish(self, request, queryset):
        c = queryset.update(is_published=False)
        self.message_user(request, f"{c} ta Short yashirildi.")

    @admin.action(description="🤖 AI generatsiya — orqa fonda (tanlangan Shorts)")
    def action_ai_generate(self, request, queryset):
        """Tanlangan Shorts uchun AI to'ldirishni ORQA FONGA qo'yadi. Ilgari
        har biri sinxron 30-90s davom etib admin ekran bloklanardi."""
        from .ai_worker import dispatch_job, enqueue_whisper
        from .models import AIJob
        ok = 0
        for s in queryset:
            try:
                job, _ = enqueue_whisper(AIJob.Kind.SHORT, s.pk)
                dispatch_job(job)
                ok += 1
            except Exception as exc:
                self.message_user(request, f"⚠ {s}: {exc}", level=messages.ERROR)
        if ok:
            self.message_user(
                request,
                f"🤖 {ok} ta Short AI navbatga qo'yildi (orqa fonda tayyorlanadi).",
                level=messages.SUCCESS,
            )


# --- Har video turi uchun alohida admin bo'limi (Short proxy) ------------
# Bazaviy `ShortAdmin` "Umumiy videolar" nomi bilan qoladi (barcha turdagi
# videolarni bitta yerda ko'rish, `content_type` ni ro'yxatda inline
# almashtirish uchun). Ustidan proxy'lar — har turga alohida "Qo'shish".

def _make_short_proxy_admin(proxy_cls):
    forced = getattr(proxy_cls, "_forced_content_type", "") or ""

    class ProxyShortAdmin(ShortAdmin):
        # `content_type` proxy o'zi filtrlab beradi — ro'yxatda ustun kerak
        # emas va inline almashtirish ham shart emas (proxy ichida bir xil).
        list_display = tuple(
            f for f in ShortAdmin.list_display if f != "content_type"
        )
        list_editable = ("priority",)
        list_filter = tuple(
            f for f in ShortAdmin.list_filter if f != "content_type"
        )
        # Add formada `content_type` ni tanlash shart emas — proxy avtomatik
        # to'ldiradi. Fieldsets'dan olib tashlaymiz.
        fieldsets = tuple(
            (
                name,
                {
                    **opts,
                    "fields": tuple(
                        fld for fld in opts.get("fields", ()) if fld != "content_type"
                    ),
                },
            )
            for name, opts in ShortAdmin.fieldsets
        )

        def get_queryset(self, request):
            # Proxy manager avtomatik content_type filtri qo'yadi
            return proxy_cls.objects.all()

        def get_object(self, request, object_id, from_field=None):
            try:
                return proxy_cls.objects.get(pk=object_id)
            except proxy_cls.DoesNotExist:
                return None

        def save_model(self, request, obj, form, change):
            # Bu proxy orqali yaratilgan yozuv HAR DOIM shu content_type ga
            # tegishli. Model default'i `short` bo'lgani sabab shunchaki
            # "bo'sh bo'lsa" tekshiruv ishlamaydi.
            obj.content_type = forced
            super().save_model(request, obj, form, change)

    ProxyShortAdmin.__name__ = f"{proxy_cls.__name__}Admin"
    return ProxyShortAdmin


for _proxy in SHORT_PROXIES:
    admin.site.register(_proxy, _make_short_proxy_admin(_proxy))


# --- Umumiy YouTube video ro'yxati (Short + Dictation) -------------------
# Foydalanuvchi so'radi: "qaysida youtube bilan aloqasi bo'lsa hammasi
# chiqsin". Bu admin bo'limi ikkala modeldan `youtube_link` bor bo'lgan
# yozuvlarni bir joyda ko'rsatadi. Foydalanuvchi qidiruv orqali topib,
# har birining O'Z change formasiga o'tadi (u yerda tur almashtiriladi).

@admin.register(AllYoutubeVideo)
class AllYoutubeVideoAdmin(BaseModelAdmin):
    """Custom changelist — Short + Dictation'ni bir jadvalda ko'rsatadi."""

    # Add/change/delete kerak emas — bu faqat ro'yxat
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return True  # click'da change formasiga o'tish uchun kerak

    def has_delete_permission(self, request, obj=None):
        return False

    def get_urls(self):
        from django.urls import path
        return [
            path("", self.admin_site.admin_view(self.combined_view),
                 name="catalog_allyoutubevideo_changelist"),
        ]

    def combined_view(self, request):
        """Ikkala modeldan youtube_link bor yozuvlarni yig'ib ko'rsatadi."""
        from django.template.response import TemplateResponse
        from django.contrib.admin.views.main import PAGE_VAR

        q = (request.GET.get("q") or "").strip()
        kind_filter = (request.GET.get("kind") or "").strip()
        try:
            page = max(1, int(request.GET.get(PAGE_VAR, 1)))
        except ValueError:
            page = 1
        PAGE_SIZE = 50

        items = []

        # --- Short'lar (barcha content_type) ---
        if kind_filter != "dictation":
            shorts_qs = Short.objects.exclude(youtube_link="")
            if q:
                from django.db.models import Q
                shorts_qs = shorts_qs.filter(
                    Q(title__icontains=q) | Q(youtube_link__icontains=q)
                    | Q(youtube_id__icontains=q) | Q(full_text__icontains=q)
                )
            for s in shorts_qs.only(
                "pk", "title", "youtube_id", "youtube_link", "content_type",
                "is_published", "views", "created_at"
            )[:500]:
                items.append({
                    "kind_label": "Short",
                    "kind_key": "short",
                    "pk": s.pk,
                    "title": s.title or f"[Short {s.youtube_id or s.pk}]",
                    "youtube_id": s.youtube_id,
                    "youtube_link": s.youtube_link,
                    "type_label": s.get_content_type_display(),
                    "is_published": s.is_published,
                    "views": s.views,
                    "created_at": s.created_at,
                    "change_url": reverse(
                        "admin:catalog_short_change", args=[s.pk]
                    ),
                })

        # --- Dictation'lar (faqat youtube_link bor bo'lganlari) ---
        if kind_filter != "short":
            dict_qs = Dictation.light.exclude(youtube_link="")
            if q:
                from django.db.models import Q
                dict_qs = dict_qs.filter(
                    Q(title__icontains=q) | Q(youtube_link__icontains=q)
                )
            for d in dict_qs.only(
                "pk", "title", "youtube_link", "type",
                "is_published", "views", "created_at"
            )[:500]:
                # youtube_id ni URL'dan chiqarib olamiz
                from . import mock_data
                yt_id = mock_data.extract_youtube_id(d.youtube_link) or ""
                items.append({
                    "kind_label": "Diktant",
                    "kind_key": "dictation",
                    "pk": d.pk,
                    "title": d.title,
                    "youtube_id": yt_id,
                    "youtube_link": d.youtube_link,
                    "type_label": d.get_type_display(),
                    "is_published": d.is_published,
                    "views": d.views,
                    "created_at": d.created_at,
                    "change_url": reverse(
                        "admin:catalog_dictation_change", args=[d.pk]
                    ),
                })

        # Sana bo'yicha teskari saralash
        items.sort(key=lambda x: x["created_at"], reverse=True)
        total = len(items)

        # Sahifalash — client tomonda
        start = (page - 1) * PAGE_SIZE
        page_items = items[start:start + PAGE_SIZE]
        total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

        context = {
            **self.admin_site.each_context(request),
            "title": "Umumiy videolar (Short + Diktant)",
            "items": page_items,
            "total": total,
            "q": q,
            "kind_filter": kind_filter,
            "page": page,
            "total_pages": total_pages,
            "has_prev": page > 1,
            "has_next": page < total_pages,
            "opts": self.model._meta,
            "app_label": self.model._meta.app_label,
        }
        return TemplateResponse(
            request,
            "admin/catalog/allyoutubevideo/change_list.html",
            context,
        )


# --- Short shikoyatlari + savol xato xabarlari ---------------------------
@admin.register(ShortReport)
class ShortReportAdmin(BaseModelAdmin):
    list_display = ("created_at", "user", "short", "reason", "short_text")
    list_filter = ("reason", "created_at")
    search_fields = (
        "user__username", "user__display_name",
        "short__title", "short__youtube_id", "text",
    )
    autocomplete_fields = ("user", "short")
    list_select_related = ("user", "short")
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "created_at"

    @admin.display(description="Izoh")
    def short_text(self, obj):
        return (obj.text or "")[:80]


@admin.register(ShortQuestionFeedback)
class ShortQuestionFeedbackAdmin(BaseModelAdmin):
    list_display = ("created_at", "user", "short", "short_text")
    list_filter = ("created_at",)
    search_fields = (
        "user__username", "user__display_name",
        "short__title", "short__youtube_id", "text",
    )
    autocomplete_fields = ("user", "short")
    list_select_related = ("user", "short")
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "created_at"

    @admin.display(description="Matn")
    def short_text(self, obj):
        return (obj.text or "")[:120]


# --- Diktant shikoyatlari + savol xato xabarlari -------------------------
@admin.register(DictationReport)
class DictationReportAdmin(BaseModelAdmin):
    list_display = ("created_at", "user", "dictation", "reason", "short_text")
    list_filter = ("reason", "created_at")
    search_fields = (
        "user__username", "user__display_name",
        "dictation__title", "dictation__slug", "text",
    )
    autocomplete_fields = ("user",)
    list_select_related = ("user", "dictation")
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "created_at"

    @admin.display(description="Izoh")
    def short_text(self, obj):
        return (obj.text or "")[:80]


@admin.register(DictationQuestionFeedback)
class DictationQuestionFeedbackAdmin(BaseModelAdmin):
    list_display = ("created_at", "user", "dictation", "short_text")
    list_filter = ("created_at",)
    search_fields = (
        "user__username", "user__display_name",
        "dictation__title", "dictation__slug", "text",
    )
    autocomplete_fields = ("user",)
    list_select_related = ("user", "dictation")
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "created_at"

    @admin.display(description="Matn")
    def short_text(self, obj):
        return (obj.text or "")[:120]


@admin.register(DeadVideoReport)
class DeadVideoReportAdmin(BaseModelAdmin):
    """O'lik YouTube video shikoyatlari — Shorts va Diktantlar uchun umumiy."""
    list_display = (
        "created_at", "verified_badge", "user", "youtube_id",
        "source_col", "verify_result",
    )
    list_filter = ("verified", "verify_result", "created_at")
    search_fields = (
        "user__username", "user__display_name",
        "youtube_url", "youtube_id",
        "short__title", "dictation__title",
    )
    autocomplete_fields = ("user", "short", "dictation")
    list_select_related = ("user", "short", "dictation")
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "created_at"
    actions = ("action_purge_verified",)

    @admin.display(description="Manba", ordering="short")
    def source_col(self, obj):
        if obj.short:
            return format_html(
                '<span title="{}">Short #{}</span>',
                obj.short.title[:80], obj.short.pk,
            )
        if obj.dictation:
            return format_html(
                '<span title="{}">Diktant #{}</span>',
                obj.dictation.title[:80], obj.dictation.pk,
            )
        return "—"

    @admin.display(description="Tasdiq", ordering="verified", boolean=True)
    def verified_badge(self, obj):
        return obj.verified

    @admin.action(description="☠ Tasdiqlanganlar manba videosini BUTUNLAY o'chirish")
    def action_purge_verified(self, request, queryset):
        """Tanlangan yozuvlar orasidan `verified=True` bo'lganlarining manba
        video (Short/Dictation)ni to'liq o'chiradi. Report'lar ham cascade
        orqali ketadi.
        """
        verified_qs = queryset.filter(verified=True)
        if not verified_qs.exists():
            self.message_user(
                request, "Tanlanganlar orasida tasdiqlanmagan (verified=False) "
                "faqat yozuvlar bor — hech narsa qilinmadi.",
                level=messages.WARNING,
            )
            return
        short_ids = set(verified_qs.exclude(short=None).values_list("short_id", flat=True))
        dictation_ids = set(verified_qs.exclude(dictation=None).values_list("dictation_id", flat=True))
        s_count, _ = Short.objects.filter(pk__in=short_ids).delete()
        d_count, _ = Dictation.objects.filter(pk__in=dictation_ids).delete()
        self.message_user(
            request,
            f"☠ Tasdiqlangan yozuvlar bo'yicha: {s_count} Short + {d_count} Diktant "
            "yozuvi butunlay o'chirildi (report'lar cascade orqali ketdi).",
            level=messages.SUCCESS,
        )


@admin.register(AIJob)
class AIJobAdmin(BaseModelAdmin):
    list_display = ("created_at", "kind", "object_id", "step", "status",
                    "attempts", "short_error")
    list_filter = ("status", "kind", "step", "created_at")
    search_fields = ("object_id", "error")
    readonly_fields = ("started_at", "finished_at", "created_at", "updated_at")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    actions = ("action_retry",)

    @admin.display(description="Xato (qisqa)")
    def short_error(self, obj):
        return (obj.error or "")[:80]

    @admin.action(description="↻ Qayta ishga tushirish (pending qilish)")
    def action_retry(self, request, queryset):
        from .ai_worker import dispatch_job
        ids = list(queryset.exclude(status=AIJob.Status.RUNNING)
                   .values_list("pk", flat=True))
        n = AIJob.objects.filter(pk__in=ids).update(
            status=AIJob.Status.PENDING, error="",
            started_at=None, finished_at=None,
        )
        # Celery rejimida darrov jo'natamiz (aks holda 20s beat-sweep kutadi).
        for job in AIJob.objects.filter(pk__in=ids, status=AIJob.Status.PENDING):
            dispatch_job(job)
        self.message_user(request, f"↻ {n} ta job qayta pending qilindi.")


@admin.register(ChannelIngestTask)
class ChannelIngestTaskAdmin(BaseModelAdmin):
    """Kanaldan video yig'ish taski.

    Foydalanish:
      1. Bo'limni tanlang: Shorts / Filmlar / Multfilmlar / Yangiliklar /
         Tasodifiy videolar (video ko'rsatiladigan 5 bo'lim).
      2. Kanal URL ni yozing — masalan
         https://www.youtube.com/@WhiteHouse/videos
         (yoki .../shorts agar Shorts bo'limi uchun).
      3. N ni bering (10 = eng yangi 10 ta yangi videoni oling).
      4. Saqlang — qolgan hamma narsa fon rejimida bajariladi.
         Bazadagi videolar tashlab yuboriladi, jami N ta YANGI kontent
         yig'iladi va har biriga AI pipeline avtomatik ishga tushadi.
    """

    list_display = ("created_at", "target_kind", "count", "status_badge",
                    "channel_short", "created_count", "skipped_count",
                    "short_error")
    list_filter = ("status", "target_kind", "created_at")
    search_fields = ("channel_url", "error")
    readonly_fields = ("status", "error", "videos_created", "videos_skipped",
                       "started_at", "finished_at", "created_at", "updated_at")
    ordering = ("-created_at",)
    actions = ("action_retry",)

    fieldsets = (
        ("Task", {
            "fields": ("target_kind", "channel_url", "count"),
            "description": (
                "Video ko'rsatiladigan 5 bo'limdan birini tanlang, kanal "
                "URL va N (video soni) bering. Bo'lim = Shorts bo'lsa URL "
                "ga /shorts qo'shing, aks holda /videos."
            ),
        }),
        ("Holat", {
            "fields": ("status", "error", "started_at", "finished_at"),
            "description": "Saqlagach avtomatik ishga tushadi.",
        }),
        ("Natija", {
            "fields": ("videos_created", "videos_skipped"),
            "classes": ("collapse",),
            "description": "Yaratilgan va tashlab yuborilgan video ID ro'yxati.",
        }),
        ("Sanalar", {
            "fields": ("created_at", "updated_at"), "classes": ("collapse",),
        }),
    )

    @admin.display(description="Holat", ordering="status")
    def status_badge(self, obj):
        color = {
            "pending": "#64748B", "running": "#2563EB",
            "done": "#10B981", "partial": "#F59E0B", "failed": "#DC2626",
        }.get(obj.status, "#64748B")
        return format_html(
            '<span style="background:{};color:#FFF;padding:2px 8px;'
            'border-radius:10px;font-weight:700;font-size:11px;">{}</span>',
            color, obj.get_status_display(),
        )

    @admin.display(description="Kanal")
    def channel_short(self, obj):
        url = obj.channel_url or ""
        short = url.replace("https://www.youtube.com/", "").replace(
            "https://youtube.com/", "")[:40]
        return format_html('<a href="{}" target="_blank">{}</a>', url, short or url)

    @admin.display(description="Yaratildi", ordering=None)
    def created_count(self, obj):
        return len(obj.videos_created or [])

    @admin.display(description="Skip", ordering=None)
    def skipped_count(self, obj):
        return len(obj.videos_skipped or [])

    @admin.display(description="Xato (qisqa)")
    def short_error(self, obj):
        return (obj.error or "")[:80]

    @admin.action(description="↻ Qayta ishga tushirish (pending qilish)")
    def action_retry(self, request, queryset):
        """Failed/partial/done bo'lganlarni qayta ishga tushirish."""
        ids = list(queryset.exclude(status=ChannelIngestTask.Status.RUNNING)
                   .values_list("pk", flat=True))
        n = ChannelIngestTask.objects.filter(pk__in=ids).update(
            status=ChannelIngestTask.Status.PENDING, error="",
            videos_created=[], videos_skipped=[],
            started_at=None, finished_at=None,
        )
        # `.update()` signal chaqirmaydi — AIJob'ni qo'lda yaratamiz + jo'natamiz.
        from .ai_worker import dispatch_job, enqueue_channel_ingest
        for pk in ids:
            job, _ = enqueue_channel_ingest(pk)
            dispatch_job(job)
        self.message_user(request, f"↻ {n} ta task qayta pending qilindi.")


@admin.register(ShortReaction)
class ShortReactionAdmin(BaseModelAdmin):
    list_display = ("user", "short", "value", "created_at")
    list_filter = ("value", "created_at")
    search_fields = ("user__username", "short__title")
    readonly_fields = ("created_at", "updated_at")


@admin.register(DictationReaction)
class DictationReactionAdmin(BaseModelAdmin):
    list_display = ("user", "dictation", "value", "created_at")
    list_filter = ("value", "created_at")
    search_fields = ("user__username", "dictation__title")
    readonly_fields = ("created_at", "updated_at")


# --- IELTS Listening test admin --------------------------------------------
#
# Ish oqimi:
#   1. Admin `source_url` beradi, saqlaydi. Post-save signal parser AIJob
#      yaratadi va orqa fonda ishga tushiradi.
#   2. Change formada yashil/qizil banner — status (`pending` → `parsing`
#      → `parsed` yoki `failed`).
#   3. `parsed` bo'lgach admin 1..N javob maydonlarini to'ldiradi. Vergul
#      bilan bir necha to'g'ri javob: `Monday, monday`. Bo'sh qoldirilsa
#      savol javobsiz.
#   4. Barcha savol to'ldirilgach `is_ready=True` (property) va admin
#      `is_published` ni yoqadi.

class AnswerVariantsWidget(forms.Widget):
    """Bir savol uchun bir necha variantli javob kirituvchi widget.

    UI: har variant uchun kichik matn maydoni + qizil × (o'chirish) tugmasi.
    Oxirida "+ variant qo'shish" tugmasi — bosilganda yangi bo'sh maydon
    qo'shadi. Bo'sh variantlar saqlashda tashlab yuboriladi.

    POST'da bir xil nomli bir necha input yuboriladi — `getlist(name)` bilan
    olamiz. Server javob ro'yxatini shu tartibda saqlaydi.
    """

    def format_value(self, value):
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [value] if value.strip() else []
        return [str(v) for v in value if str(v).strip()]

    def value_from_datadict(self, data, files, name):
        if hasattr(data, "getlist"):
            values = [v.strip() for v in data.getlist(name) if v and v.strip()]
        else:
            v = data.get(name)
            values = [v.strip()] if v and v.strip() else []
        return values

    def render(self, name, value, attrs=None, renderer=None):
        from django.utils.html import format_html, format_html_join
        variants = self.format_value(value)
        if not variants:
            variants = [""]  # kamida bitta bo'sh maydon
        rows = format_html_join(
            "",
            '<div class="ielts-ans-row" style="display:flex;gap:6px;margin-bottom:4px;">'
            '<input type="text" name="{}" value="{}" '
            'style="flex:1;padding:5px 9px;font-family:\'Menlo\',\'Consolas\',monospace;'
            'border:1px solid #b8bfcb;border-radius:4px;font-size:13px;" '
            'placeholder="javob varianti">'
            '<button type="button" class="ielts-ans-del" '
            'style="width:30px;background:#FEE2E2;border:1px solid #FCA5A5;'
            'color:#991B1B;border-radius:4px;cursor:pointer;font-weight:800;'
            'font-size:14px;">×</button>'
            "</div>",
            ((name, v) for v in variants),
        )
        add_btn = format_html(
            '<button type="button" class="ielts-ans-add" data-name="{}" '
            'style="background:#DBEAFE;border:1px solid #93C5FD;color:#1E3A8A;'
            'padding:5px 12px;border-radius:4px;font-weight:700;cursor:pointer;'
            'font-size:12px;margin-top:2px;">+ variant qo\'shish</button>',
            name,
        )
        return format_html(
            '<div class="ielts-ans-multi" style="max-width:420px;">{}{}</div>',
            rows, add_btn,
        )


class IeltsAnswersForm(forms.ModelForm):
    """Har savol uchun alohida `answer_1..answer_N` — bir necha variantli
    javob (widget yuqorida). Saqlashda `answers` JSON'ga birlashadi."""

    class Meta:
        model = IeltsListeningTest
        fields = ("source_url", "title", "slug", "total_questions", "is_published")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        instance: IeltsListeningTest | None = self.instance if self.instance and self.instance.pk else None
        # Yangi obyekt yaratayotganda javob maydonlari kerak emas — admin avval
        # URL ni saqlaydi, parser ishlab bo'lgach change formda javoblarni yozadi.
        if not instance:
            return
        total = int(instance.total_questions or 40)
        answers = instance.answers or {}
        for q in range(1, total + 1):
            key = f"answer_{q}"
            existing = answers.get(str(q)) or answers.get(q) or []
            if isinstance(existing, str):
                existing = [existing]
            initial_list = [str(v) for v in existing if str(v).strip()]
            self.fields[key] = forms.Field(
                label=f"Savol {q}",
                required=False,
                initial=initial_list,
                widget=AnswerVariantsWidget(),
                help_text=(
                    "Har variantni alohida maydonga yozing. \"+ variant qo'shish\" "
                    "bilan yangi variant qo'shing. Case va tinish belgilariga "
                    "e'tibor berilmaydi."
                ) if q == 1 else "",
            )

    def clean(self):
        cleaned = super().clean()
        if not (self.instance and self.instance.pk):
            return cleaned
        total = int(self.instance.total_questions or 40)
        new_answers: dict[str, list[str]] = {}
        for q in range(1, total + 1):
            variants = cleaned.get(f"answer_{q}") or []
            if isinstance(variants, str):
                variants = [variants]
            variants = [v.strip() for v in variants if v and v.strip()]
            if variants:
                new_answers[str(q)] = variants
        cleaned["_answers_map"] = new_answers
        return cleaned

    def save(self, commit=True):
        obj = super().save(commit=False)
        if self.instance and self.instance.pk:
            obj.answers = self.cleaned_data.get("_answers_map") or {}
            # is_ready property — barcha 1..N ga javob bo'lsa avtomatik READY.
            if obj.status == IeltsListeningTest.Status.PARSED and obj.is_ready:
                obj.status = IeltsListeningTest.Status.READY
            elif obj.status == IeltsListeningTest.Status.READY and not obj.is_ready:
                # Admin biror javobni bo'shatib qo'ysa — orqaga PARSED
                obj.status = IeltsListeningTest.Status.PARSED
        if commit:
            obj.save()
        return obj


class IeltsAddForm(forms.ModelForm):
    """Yangi test yaratish — faqat URL. Parser qolganini avtomatik oladi."""

    class Meta:
        model = IeltsListeningTest
        fields = ("source_url",)


@admin.register(IeltsListeningTest)
class IeltsListeningTestAdmin(BaseModelAdmin):
    list_display = (
        "title_col", "status_badge", "answered_col",
        "total_questions", "is_published", "views", "created_at",
    )
    list_filter = ("status", "is_published", "created_at")
    search_fields = ("title", "source_url", "slug")
    date_hierarchy = "created_at"
    readonly_fields = ("status", "parse_error",
                       "views", "created_at", "updated_at")
    change_form_template = "admin/catalog/ieltslisteningtest/change_form.html"
    actions = ("action_publish", "action_unpublish", "action_rebuild_html", "action_reparse")

    def get_form(self, request, obj=None, **kwargs):
        if obj is None:
            return IeltsAddForm
        return IeltsAnswersForm

    def get_fieldsets(self, request, obj=None):
        if obj is None:
            return (
                (None, {
                    "fields": ("source_url",),
                    "description": (
                        "engnovate.com IELTS listening testining sahifa URL manzilini kiriting. "
                        "Saqlagach parser orqa fonda ishga tushadi va HTML olib beradi."
                    ),
                }),
            )
        # Change form — javoblar
        answer_fields = tuple(f"answer_{q}" for q in range(1, (obj.total_questions or 40) + 1))
        return (
            (None, {"fields": ("source_url", "title", "slug", "total_questions", "is_published")}),
            ("Parser holati", {"fields": ("status", "parse_error")}),
            ("Javoblar (1..N)", {
                "fields": answer_fields,
                "description": (
                    "Har savol uchun kamida bitta to'g'ri javob yozing. Vergul "
                    "bilan bir necha to'g'ri variant qabul qilinadi (masalan: "
                    "<code>Monday, monday</code>). Case va tinish belgilariga "
                    "e'tibor berilmaydi. <b>Barcha savolga javob yozilgach</b> "
                    "sayt uni tayyor deb hisoblaydi va `is_published` ni yoqishingiz mumkin."
                ),
            }),
            ("Meta", {"fields": ("views", "created_at", "updated_at"),
                       "classes": ("collapse",)}),
        )

    @admin.display(description="Sarlavha")
    def title_col(self, obj):
        return obj.title or f"IELTS #{obj.pk}"

    @admin.display(description="Holat")
    def status_badge(self, obj):
        palette = {
            "pending": ("#94A3B8", "Kutayapti"),
            "parsing": ("#F59E0B", "⏳ Parse..."),
            "parsed": ("#2563EB", "📝 Javob kutilmoqda"),
            "ready": ("#059669", "✓ Tayyor"),
            "failed": ("#EF4444", "⚠ Xato"),
        }
        color, label = palette.get(obj.status, ("#94A3B8", obj.status))
        return format_html('<span style="color:{};font-weight:700">{}</span>', color, label)

    @admin.display(description="Javoblar")
    def answered_col(self, obj):
        total = obj.total_questions or 0
        n = obj.answered_count
        color = "#059669" if n >= total and total > 0 else "#F59E0B" if n > 0 else "#94A3B8"
        return format_html(
            '<span style="color:{};font-weight:700">{} / {}</span>', color, n, total,
        )

    @admin.action(description="Chop etish")
    def action_publish(self, request, queryset):
        # Faqat javoblari to'liq bo'lganlarni chop etamiz.
        ok = 0
        for obj in queryset:
            if obj.is_ready:
                obj.is_published = True
                obj.save(update_fields=["is_published", "updated_at"])
                ok += 1
        self.message_user(request, f"{ok} ta test chop etildi (faqat javoblari to'liq bo'lganlari).")

    @admin.action(description="Chop etishni bekor qilish")
    def action_unpublish(self, request, queryset):
        n = queryset.update(is_published=False)
        self.message_user(request, f"{n} ta test yashirildi.")

    @admin.action(description="↻ Qayta parse (HTML ni qayta olish)")
    @admin.action(description="🔄 Sahifani qayta yasash (plyerni yangilash)")
    def action_rebuild_html(self, request, queryset):
        """HTML ni JORIY shablon bilan qayta yasaydi.

        `html` bazada parse paytida qotib qoladi, ya'ni plyerga qo'shilgan
        yangilik (masalan audio pozitsiyasini surish paneli) eski testlarga
        yetib bormaydi. Bu amal aynan shuni tuzatadi.

        `parts_json` bor bo'lsa TARMOQQA CHIQMAYDI (tez). Bo'lmasa manba
        sahifa bir marta qayta olinadi. Savollar va javoblar tegilmaydi.
        """
        from .ielts_parser import rebuild_html

        ok = failed = 0
        for obj in queryset:
            try:
                if rebuild_html(obj):
                    ok += 1
                else:
                    failed += 1
            except Exception as exc:
                failed += 1
                self.message_user(request, f"#{obj.pk}: {exc}", level=messages.ERROR)
        self.message_user(
            request,
            f"🔄 {ok} ta sahifa qayta yasaldi"
            + (f", {failed} tasida xato." if failed else "."),
        )

    def action_reparse(self, request, queryset):
        from .ai_worker import dispatch_job, enqueue_ielts_parse
        n = 0
        for obj in queryset:
            # HTML ni bo'shatamiz — signal ham parse'ni yoqadi, lekin baribir
            # qo'lda ham yuborib qo'yamiz.
            obj.html = ""
            obj.status = IeltsListeningTest.Status.PENDING
            obj.parse_error = ""
            obj.save(update_fields=["html", "status", "parse_error", "updated_at"])
            job, _ = enqueue_ielts_parse(obj.pk)
            dispatch_job(job)
            n += 1
        self.message_user(request, f"↻ {n} ta test qayta parse navbatiga qo'yildi.")

    def response_add(self, request, obj, post_url_continue=None):
        # URL saqlangach darrov change formga o'tsin — foydalanuvchi javoblarni
        # ko'radi va parser statusini kuzatadi.
        return HttpResponseRedirect(
            reverse("admin:catalog_ieltslisteningtest_change", args=[obj.pk])
        )

    def change_view(self, request, object_id, form_url="", extra_context=None):
        extra_context = extra_context or {}
        try:
            obj = IeltsListeningTest.objects.get(pk=object_id)
        except (IeltsListeningTest.DoesNotExist, ValueError):
            obj = None
        extra_context["ielts_obj"] = obj
        return super().change_view(request, object_id, form_url, extra_context)

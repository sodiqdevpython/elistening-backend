"""Diktant serializerlari."""
from rest_framework import serializers

from apps.common.protect import ProtectedFieldsMixin

from .models import (
    Dictation, DictationProgress, DictationQuestionFeedback, DictationReport,
    IeltsListeningTest, Short, ShortQuestionFeedback, ShortReport,
)


class DictationBodyChunkSerializer(serializers.Serializer):
    """`Dictation.body` ichidagi bitta chunk sxemasi (dokumentatsiya uchun)."""

    start_ms = serializers.IntegerField(min_value=0)
    end_ms = serializers.IntegerField(min_value=0)
    text = serializers.CharField()


class DictationListSerializer(serializers.ModelSerializer):
    """Ro'yxatda ishlatiladi — body ni yubormaymiz (kichikroq javob).

    Progress ro'yxatda ko'rsatilmaydi (har chunk'da yozib borish qimmatga
    tushadi). Kartochka sodda: sarlavha, daraja, davomiylik, thumbnail.
    """

    type_label = serializers.CharField(source="get_type_display", read_only=True)
    type_slug = serializers.SerializerMethodField()
    audio_url = serializers.SerializerMethodField()
    duration_sec = serializers.IntegerField(read_only=True)
    duration_label = serializers.CharField(read_only=True)
    chunks_count = serializers.IntegerField(read_only=True)
    # Frontend YouTube thumbnail'ni `i.ytimg.com` dan yuklashi uchun ID kerak.
    # URL turlaridan (watch?v=, youtu.be/, /shorts/, /embed/) ajratib olamiz.
    youtube_id = serializers.SerializerMethodField()

    class Meta:
        model = Dictation
        fields = (
            "id", "slug", "title", "type", "type_label", "type_slug", "cefr_level",
            "audio_url", "is_media", "youtube_link", "youtube_id",
            "duration_sec", "duration_label", "chunks_count",
            "views", "likes", "dislikes", "practiced_time", "created_at",
        )

    def get_youtube_id(self, obj) -> str | None:
        from . import mock_data
        if not obj.is_media:
            return None
        return mock_data.extract_youtube_id(obj.youtube_link)

    def get_type_slug(self, obj) -> str:
        """`short_story` → `short-stories` (URL uchun kategoriya slug)."""
        from . import mock_data
        for meta in mock_data.CATEGORY_META:
            if meta[-1] == obj.type:
                return meta[0]
        return obj.type.replace("_", "-")

    def get_audio_url(self, obj):
        if not obj.audio:
            return None
        request = self.context.get("request")
        url = obj.audio.url
        return request.build_absolute_uri(url) if request else url


class DictationDetailSerializer(ProtectedFieldsMixin, DictationListSerializer):
    """Batafsil ko'rinish — body (chunk timestamp) + words_json (so'z timestamp)
    + AI-generatsiya qilingan listening test savollari.

    Transkript va savollar javobda **o'ralgan** holda ketadi: ular
    `fields` da qoladi (sxema/dokumentatsiya buzilmaydi), lekin javobda
    bitta `enc` satriga aylanadi (`apps/common/protect.py`). Mijoz uni
    avtomatik ochadi — komponentlar o'zgarmaydi.
    """

    #: `enc` ichiga kiradigan maydonlar (qolgani ochiq: sarlavha, daraja, ...).
    PROTECTED_FIELDS = (
        "body", "words_json",
        "mcq_questions", "tfng_questions", "fill_gap_questions",
    )

    body = serializers.JSONField()
    words_json = serializers.JSONField()
    mcq_questions = serializers.JSONField()
    tfng_questions = serializers.JSONField()
    fill_gap_questions = serializers.JSONField()

    class Meta(DictationListSerializer.Meta):
        fields = DictationListSerializer.Meta.fields + (
            "body", "words_json",
            "mcq_questions", "tfng_questions", "fill_gap_questions",
        )


class DictationProgressWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = DictationProgress
        fields = ("percent", "last_index", "draft_answers")


class ShortListSerializer(ProtectedFieldsMixin, serializers.ModelSerializer):
    """Shorts feed uchun yengil ko'rinish — savollar bilan (frontend darrov
    videoni ochib javob berishi uchun).

    Savollar javobda **o'ralgan** (`enc`) — `DictationDetailSerializer` bilan
    bir xil qoida.
    """

    PROTECTED_FIELDS = ("mcq_questions", "tfng_questions", "fill_gap_questions")

    class Meta:
        model = Short
        fields = (
            "id", "content_type", "youtube_id", "youtube_link", "title", "duration_sec",
            "cefr_from", "cefr_to", "tags",
            "mcq_questions", "tfng_questions", "fill_gap_questions",
            "views", "likes", "dislikes", "created_at",
        )


class ShortReportWriteSerializer(serializers.ModelSerializer):
    """Foydalanuvchi POST qiladigan Short shikoyati."""
    class Meta:
        model = ShortReport
        fields = ("reason", "text")


class ShortQuestionFeedbackWriteSerializer(serializers.ModelSerializer):
    """Foydalanuvchi POST qiladigan savol xatolik xabari."""
    class Meta:
        model = ShortQuestionFeedback
        fields = ("text",)

    def validate_text(self, v: str) -> str:
        v = (v or "").strip()
        if len(v) < 3:
            raise serializers.ValidationError("Iltimos, batafsilroq yozing (kamida 3 belgi).")
        return v


class DictationReportWriteSerializer(serializers.ModelSerializer):
    """Foydalanuvchi POST qiladigan diktant shikoyati."""
    class Meta:
        model = DictationReport
        fields = ("reason", "text")


class DictationQuestionFeedbackWriteSerializer(serializers.ModelSerializer):
    """Foydalanuvchi POST qiladigan diktant savol xatolik xabari."""
    class Meta:
        model = DictationQuestionFeedback
        fields = ("text",)

    def validate_text(self, v: str) -> str:
        v = (v or "").strip()
        if len(v) < 3:
            raise serializers.ValidationError("Iltimos, batafsilroq yozing (kamida 3 belgi).")
        return v


class IeltsListeningTestListSerializer(serializers.ModelSerializer):
    """Ro'yxatdagi karta uchun — HTML yubormaymiz. `my_result` bo'lsa test
    "bajarilgan" (ro'yxatda belgi + ball ko'rsatiladi)."""
    my_result = serializers.SerializerMethodField()

    class Meta:
        model = IeltsListeningTest
        fields = (
            "id", "slug", "title", "total_questions", "views", "created_at",
            "my_result",
        )

    def get_my_result(self, obj):
        # Viewset kontekstga `my_results` (test_id -> {score,total}) qo'yadi.
        r = (self.context.get("my_results") or {}).get(obj.id)
        return {"score": r.score, "total": r.total} if r else None


class IeltsListeningTestDetailSerializer(serializers.ModelSerializer):
    """Detail — HTML bilan (frontend iframe srcdoc'ga o'rnatadi). Javoblar
    yuborilmaydi (aks holda foydalanuvchi ko'rib topshirmasdan bilib olardi).
    `my_result` — foydalanuvchining oxirgi natijasi (qaytganda ko'rsatiladi)."""
    my_result = serializers.SerializerMethodField()

    class Meta:
        model = IeltsListeningTest
        fields = (
            "id", "slug", "title", "total_questions", "html",
            "views", "created_at", "my_result",
        )

    def get_my_result(self, obj):
        r = (self.context.get("my_results") or {}).get(obj.id)
        if not r:
            return None
        return {"score": r.score, "total": r.total, "results": r.results_json or {}}

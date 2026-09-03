"""Diktantlar uchun API."""
from django.db.models import Count, F
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.pagination import StandardPageNumberPagination

from .models import (
    DeadVideoReport, Dictation, DictationProgress, DictationQuestionFeedback,
    DictationReport, IeltsListeningTest, IeltsListeningTestResult, Short,
    ShortQuestionFeedback, ShortReport,
)
from .serializers import (
    DictationDetailSerializer, DictationListSerializer,
    DictationProgressWriteSerializer, DictationQuestionFeedbackWriteSerializer,
    DictationReportWriteSerializer,
    IeltsListeningTestDetailSerializer, IeltsListeningTestListSerializer,
    ShortListSerializer,
    ShortQuestionFeedbackWriteSerializer, ShortReportWriteSerializer,
)


class DictationViewSet(viewsets.ReadOnlyModelViewSet):
    """Diktantlar — ro'yxat va batafsil ko'rinish.

    Query params:
        ?type=short_story          — mavzu bo'yicha filtr
        ?level=B1                  — daraja bo'yicha filtr
        ?search=hello              — sarlavha bo'yicha qidiruv
        ?page=1&page_size=20       — sahifalash
        ?exclude=1,2,3             — bu id'larni tashlab ketish (ko'rilganlar)
        ?random=1                  — tasodifiy tartib (aks holda eng yangisi)
        ?media=1                   — FAQAT YouTube videosi borlari (mobil ilova)
        ?exclude_type=news         — bu turlarni CHIQARIB tashlaydi (vergul bilan)

    Detail endpoint slug **yoki id** bilan qidiradi — `/dictations/booking-a-room/`
    ham, `/dictations/13/` ham ishlaydi. Frontend LessonsPage numeric id
    beradi (eski route), DictationsPage esa slug beradi.
    """

    lookup_field = "slug"
    lookup_value_regex = r"[-a-zA-Z0-9_]+"
    pagination_class = StandardPageNumberPagination
    search_fields = ("title",)
    ordering_fields = ("created_at", "views", "practiced_time", "title")

    def get_object(self):
        """slug yoki id bilan qidirish (raqam bo'lsa pk, aks holda slug)."""
        queryset = self.filter_queryset(self.get_queryset())
        lookup = self.kwargs.get(self.lookup_field) or self.kwargs.get("pk")
        if lookup and str(lookup).isdigit():
            obj = get_object_or_404(queryset, pk=int(lookup))
        else:
            obj = get_object_or_404(queryset, slug=lookup)
        self.check_object_permissions(self.request, obj)
        return obj

    def get_queryset(self):
        # Foydalanuvchiga faqat AI transkript tayyor bo'lgan diktantlarni
        # ko'rsatamiz. Aks holda "bo'sh" diktantlar ochilib chalkashlik.
        qs = Dictation.objects.filter(is_published=True).exclude(body=[])
        type_filter = self.request.query_params.get("type")
        if type_filter:
            # `?type=` ikkala formatni qabul qiladi:
            #   `short_story` (Dictation.Type.value) yoki
            #   `short-stories` (URL slug — CATEGORY_META).
            # Frontend URL doim slug bilan yuboradi, `-` bo'lsa slug deb hisoblaymiz.
            if "-" in type_filter:
                from . import mock_data
                mapped = mock_data.dictation_type_for_slug(type_filter)
                if mapped:
                    type_filter = mapped
            qs = qs.filter(type=type_filter)
        level_filter = self.request.query_params.get("level")
        if level_filter and level_filter != "all":
            qs = qs.filter(cefr_level=level_filter)
        search = self.request.query_params.get("search")
        if search:
            qs = qs.filter(title__icontains=search)

        # `?exclude_type=news,ielts` — bu turlarni chiqarib tashlaydi.
        #
        # Mobil bosh sahifadagi shablon uchun: "videolar" bloki — news BO'LMAGAN
        # videolar, "news" bloki esa alohida (`?type=news`). `?type=` faqat
        # kirituvchi filtr bo'lgani sabab teskarisi ham kerak bo'ldi.
        exclude_type = self.request.query_params.get("exclude_type")
        if exclude_type:
            kinds = [x.strip() for x in exclude_type.split(",") if x.strip()]
            if kinds:
                qs = qs.exclude(type__in=kinds)

        # `?media=1` — faqat YouTube videosi bor diktantlar.
        #
        # Mobil ilovada FAQAT YouTube kontenti bo'ladi (diktant mashqi u yerda
        # yo'q), shu bois u har so'rovda shu bayroqni yuboradi. Sayt esa
        # bayroqsiz so'raydi va hamma diktantni ko'radi — eski xulq buzilmaydi.
        if self.request.query_params.get("media") == "1":
            qs = qs.filter(is_media=True).exclude(youtube_link="")

        # `?exclude=1,2,3` — chaqiruvchi allaqachon ko'rsatgan id'lar.
        # Mobil ilovadagi aralash lenta (shorts + videolar) shu bilan
        # takrorlanmaydi: har sahifa oldingilarini `exclude=` ga qo'shadi.
        # `ShortViewSet` dagi bilan bir xil shakl — ikkalasi bir xil ishlaydi.
        exclude = self.request.query_params.get("exclude")
        if exclude:
            ids: list[int] = []
            for tok in exclude.split(","):
                tok = tok.strip()
                if tok.isdigit():
                    ids.append(int(tok))
            if ids:
                qs = qs.exclude(id__in=ids)

        # `?random=1` — har kirishda boshqacha tartib (mobil bosh sahifa).
        # Berilmasa — eski xulq: eng yangisi birinchi (sayt shunga tayanadi).
        if self.request.query_params.get("random") == "1":
            return qs.order_by("?")
        return qs.order_by("-created_at")

    def get_serializer_class(self):
        return DictationDetailSerializer if self.action == "retrieve" else DictationListSerializer

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        # `views` ni oshirib qo'yamiz — race'siz atomic update.
        Dictation.objects.filter(pk=instance.pk).update(views=instance.views + 1)
        instance.views += 1
        return Response(self.get_serializer(instance, context={"request": request}).data)

    @action(detail=True, methods=["get", "post"], permission_classes=[IsAuthenticated],
            url_path="progress")
    def progress(self, request, slug=None):
        """Foydalanuvchi progressi.

        GET  → hozirgi progress (percent, last_index, draft_answers)
        POST → yangilash (yoki yaratish). Frontend har 1-2 s da yuboradi.
        """
        dictation = self.get_object()
        if request.method == "GET":
            entry = DictationProgress.objects.filter(user=request.user, dictation=dictation).first()
            if not entry:
                return Response({"percent": 0, "last_index": 0, "draft_answers": {}})
            return Response(DictationProgressWriteSerializer(entry).data)

        # POST — upsert
        entry, _ = DictationProgress.objects.get_or_create(
            user=request.user, dictation=dictation,
        )
        serializer = DictationProgressWriteSerializer(entry, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    # --- Shikoyat / Savol xatolik xabari ---------------------------------
    #
    # Shorts'dagi bilan bir xil shartnoma (`ShortViewSet.report` va h.k.) —
    # frontend ikkalasi uchun bitta modal komponentidan foydalanadi.
    @action(detail=False, methods=["get"], url_path="report-reasons",
            permission_classes=[])
    def report_reasons(self, request):
        """Shikoyat modali uchun sabab ro'yxati (kalit + label)."""
        return Response([
            {"key": key, "label": label}
            for key, label in DictationReport.Reason.choices
        ])

    @action(detail=True, methods=["post"], url_path="report",
            permission_classes=[IsAuthenticated])
    def report(self, request, slug=None):
        """Diktant/video haqida shikoyat (bitta user — bitta diktant — 1 marta).

        Body: `{ "reason": "sexual|violent|...", "text"?: "..." }`
        """
        dictation = self.get_object()
        if DictationReport.objects.filter(user=request.user, dictation=dictation).exists():
            return Response(
                {"detail": "Bunga allaqachon shikoyat yuborgansiz."},
                status=status.HTTP_409_CONFLICT,
            )
        serializer = DictationReportWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        DictationReport.objects.create(
            user=request.user, dictation=dictation,
            reason=serializer.validated_data["reason"],
            text=serializer.validated_data.get("text", ""),
        )
        return Response({"ok": True}, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="question-feedback",
            permission_classes=[IsAuthenticated])
    def question_feedback(self, request, slug=None):
        """Test savoli xato tuzilganligi haqida xabar (user — diktant — 1 marta)."""
        dictation = self.get_object()
        if DictationQuestionFeedback.objects.filter(
                user=request.user, dictation=dictation).exists():
            return Response(
                {"detail": "Bu savollar haqida allaqachon xabar yuborgansiz."},
                status=status.HTTP_409_CONFLICT,
            )
        serializer = DictationQuestionFeedbackWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        DictationQuestionFeedback.objects.create(
            user=request.user, dictation=dictation,
            text=serializer.validated_data["text"],
        )
        return Response({"ok": True}, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"], url_path="my-feedback",
            permission_classes=[IsAuthenticated])
    def my_feedback(self, request, slug=None):
        """Frontend tugmalarni disable qilishi uchun — nima yuborilgan +
        foydalanuvchining joriy reaksiyasi (like/dislike tugmasi holati)."""
        from .models import DictationReaction
        from .reactions import my_reaction
        dictation = self.get_object()
        return Response({
            "reported": DictationReport.objects
                .filter(user=request.user, dictation=dictation).exists(),
            "question_reported": DictationQuestionFeedback.objects
                .filter(user=request.user, dictation=dictation).exists(),
            "my_reaction": my_reaction(
                target=dictation, reaction_model=DictationReaction,
                fk_name="dictation", user=request.user,
            ),
            "likes": dictation.likes,
            "dislikes": dictation.dislikes,
        })

    @action(detail=True, methods=["post"], url_path="react",
            permission_classes=[IsAuthenticated])
    def react(self, request, slug=None):
        """Diktant/video'ga like/dislike — HAR USER 1 MARTA (Short bilan bir xil).

        Body: `{"reaction": "like"|"dislike"}`. Javob: `{likes, dislikes, my_reaction}`.
        """
        from .models import DictationReaction
        from .reactions import toggle_reaction
        dictation = self.get_object()
        clicked = (request.data.get("reaction") or "").strip()
        try:
            result = toggle_reaction(
                target=dictation, target_model=Dictation,
                reaction_model=DictationReaction, fk_name="dictation",
                user=request.user, clicked=clicked,
            )
        except ValueError:
            return Response(
                {"detail": "reaction 'like' yoki 'dislike' bo'lishi kerak"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(result)

    @action(detail=True, methods=["post"], url_path="view", permission_classes=[])
    def register_view(self, request, slug=None):
        """Video ko'rila boshlaganda — `views` ni oshiradi. Ro'yxatdan o'tgan
        foydalanuvchi n marta ko'rsa n marta hisoblanadi (frontend har
        boshlashda chaqiradi). Anonim ham hisoblanadi."""
        dictation = self.get_object()
        # Kunlik limit: video (default) yoki diktant (web `?kind=dictation`).
        kind = (request.data.get("kind") or request.query_params.get("kind") or "video")
        if kind not in ("video", "dictation", "ielts"):
            kind = "video"
        from apps.billing.limits import enforce_or_response
        limited = enforce_or_response(request, kind, dictation.pk)
        if limited is not None:
            return limited
        Dictation.objects.filter(pk=dictation.pk).update(views=F("views") + 1)
        return Response({"views": dictation.views + 1})

    @action(detail=True, methods=["post"], url_path="add-time")
    def add_time(self, request, slug=None):
        """Diktant ustida tinglagan vaqtni oshirib qo'yish (global).

        Frontend har audio ijro yakunlanganda POST qiladi. Kirmagan
        foydalanuvchilar ham hisoblanadi (global statistika).
        """
        dictation = self.get_object()
        try:
            ms = int(request.data.get("ms") or 0)
        except (TypeError, ValueError):
            return Response({"detail": "ms noto'g'ri"}, status=status.HTTP_400_BAD_REQUEST)
        if ms <= 0:
            return Response({"practiced_time": dictation.practiced_time})
        Dictation.objects.filter(pk=dictation.pk).update(
            practiced_time=dictation.practiced_time + ms,
        )
        return Response({"practiced_time": dictation.practiced_time + ms})


@api_view(["GET"])
def dictation_types(request):
    """Mavzular ro'yxati — har biri uchun diktantlar soni.

    Frontend TopicsPage shu ma'lumot bilan grid quradi. Foydalanuvchiga
    bog'liq emas + kamdan-kam o'zgaradi (kontent qo'shilganda) — 2 daqiqa
    keshlanadi.
    """
    from django.core.cache import cache
    data = cache.get("dictation_types_v1")
    if data is None:
        counts = dict(
            Dictation.objects.filter(is_published=True)
            .values_list("type").annotate(c=Count("id"))
        )
        data = [
            {"key": value, "label": label, "count": counts.get(value, 0)}
            for value, label in Dictation.Type.choices
        ]
        cache.set("dictation_types_v1", data, 120)
    return Response(data)


def _priority_boost(request):
    """`priority` — lekin FAQAT foydalanuvchi hali ko'rmagan videolar uchun.

    **Muammo.** Ilgari lenta shunchaki `-priority` bo'yicha saralanardi.
    Natijada priority'si baland video har kirganda yana birinchi chiqardi va
    o'chirilmaguncha o'sha yerda turardi. Foydalanuvchi aytganidek, priority
    "kamida BIR MARTA hammaga ko'rsatilsin" degani, "abadiy birinchi" emas.

    (Mo'ljal `exclude=` mijoz tomonidan kelishiga edi, lekin Shorts lentasi
    uni yubormaydi — shu bois hech qachon ishlamagan.)

    **Yechim.** Ko'rilganini SERVER biladi: `DailyUsage` har ko'rishda bitta
    qator yozadi (limit hisobi uchun) — ya'ni "bu odam bu kontentni ko'rgan"
    ma'lumoti allaqachon bor. Ko'rilgan video uchun boost 0 ga tushadi va u
    oddiy videolar qatoriga qo'shilib, tasodifiy tartibda chiqaveradi.

    Anonim foydalanuvchida tarix yo'q — priority o'z holicha ishlaydi.

    Tezlik: faqat priority'si 0 dan katta videolar tekshiriladi (ular kam),
    ya'ni so'rov foydalanuvchining butun tarixini o'qimaydi.
    """
    from django.db.models import Case, IntegerField, Value, When

    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return F("priority")

    from apps.billing.models import DailyUsage

    priority_ids = list(
        Short.objects.filter(priority__gt=0).values_list("id", flat=True)
    )
    if not priority_ids:
        return F("priority")

    seen = set(
        DailyUsage.objects
        .filter(user=user, kind="shorts", ref__in=[str(i) for i in priority_ids])
        .values_list("ref", flat=True)
    )
    seen_ids = [i for i in priority_ids if str(i) in seen]
    if not seen_ids:
        return F("priority")

    return Case(
        When(id__in=seen_ids, then=Value(0)),
        default=F("priority"),
        output_field=IntegerField(),
    )


class ShortViewSet(viewsets.ReadOnlyModelViewSet):
    """Shorts — feed va batafsil ko'rinish.

    Har element AI-generatsiya qilingan MCQ va TFNG savollarni ham o'z ichiga
    oladi — frontend darrov videoni ochib javob berishi mumkin.

    Query params:
        ?level=B1        — CEFR daraja bo'yicha filtr (cefr_from<=level<=cefr_to)
        ?search=hello    — sarlavha yoki teg bo'yicha qidirish
        ?page=1&page_size=10  — sahifalash (infinite scroll uchun)
    """
    serializer_class = ShortListSerializer
    pagination_class = StandardPageNumberPagination

    # Shikoyat / savol-xato / reaksiya / ko'rish — Short "dead" yoki nashrdan
    # chiqarilgan bo'lsa HAM ishlashi kerak. Default get_object strict feed
    # queryset'iga (DONE + not dead + published + mcq bor) qaraydi; biror shart
    # buzilsa 404 qaytarib, YOZUV yo'qolib ketardi (masalan mobil ilova YouTube
    # xatosida `mark-dead` chaqiradi → keyingi shikoyat 404). Shu amallar uchun
    # to'g'ridan-to'g'ri pk bo'yicha (barcha Shortlar ichidan) qidiramiz.
    _LENIENT_ACTIONS = {"report", "question_feedback", "react", "my_feedback", "register_view", "mark_dead"}

    def get_object(self):
        if getattr(self, "action", None) in self._LENIENT_ACTIONS:
            return get_object_or_404(Short.objects.all(), pk=self.kwargs.get("pk"))
        return super().get_object()

    def get_queryset(self):
        # Faqat AI to'la tayyor bo'lganlarni ko'rsatamiz: transkript DONE + AI
        # savollari yozilgan (kamida MCQ). Aks holda foydalanuvchi "bo'sh"
        # Shorts ko'rmaydi. Shorts pipeline'i whisper + haiku ni bir marta
        # bajaradi, shu bois transcription_status=done bo'lsa savollar ham bor.
        qs = Short.objects.filter(
            is_published=True,
            transcription_status=Short.TranscriptionStatus.DONE,
            is_dead=False,
        ).exclude(mcq_questions=[])
        # ?content_type=news|cartoon|movie|short — feed'ni ajratish uchun.
        # Default: hech qanday filtr yo'q, hammasi (backward-compat).
        ct = self.request.query_params.get("content_type")
        if ct:
            allowed = {c.value for c in Short.ContentType}
            wanted = {p.strip() for p in ct.split(",") if p.strip()}
            wanted = wanted & allowed
            if wanted:
                qs = qs.filter(content_type__in=list(wanted))
        # `?levels=B1,B2` — vergul bilan ajratilgan bir necha daraja (frontend
        # default: userning darajasi va bir baland). `?level=B1` — bitta.
        # Har biri: cefr_from <= L <= cefr_to (yoki chegara qiymati bilan teng).
        from django.db.models import Q
        levels: list[str] = []
        multi = self.request.query_params.get("levels")
        if multi:
            levels = [l.strip() for l in multi.split(",") if l.strip() and l.strip() != "all"]
        else:
            single = self.request.query_params.get("level")
            if single and single != "all":
                levels = [single]
        if levels:
            q = Q()
            for L in levels:
                q |= Q(cefr_from__lte=L, cefr_to__gte=L) | Q(cefr_from=L) | Q(cefr_to=L)
            qs = qs.filter(q)

        search = self.request.query_params.get("search")
        if search:
            qs = qs.filter(title__icontains=search)

        # `?exclude=1,2,3` — foydalanuvchi allaqachon ko'rgan Shorts id'lari.
        # Ularni tashlab yuboramiz — takrorlanmasin.
        exclude = self.request.query_params.get("exclude")
        if exclude:
            ids: list[int] = []
            for tok in exclude.split(","):
                tok = tok.strip()
                if tok.isdigit():
                    ids.append(int(tok))
            if ids:
                qs = qs.exclude(id__in=ids)

        # Priority — "KAMIDA BIR MARTA ko'rsatish", "abadiy birinchi" EMAS.
        qs = qs.annotate(boost=_priority_boost(self.request))

        # `?random=1` — lenta tartibi. Tasodifiy, LEKIN ko'rilmagan
        # priority'lilar oldinda turadi.
        #
        # `?random=0` yoki umuman berilmasa — eng yangilari birinchi
        # (news/movies/cartoons feed'lari shuni ishlatadi).
        if self.request.query_params.get("random") == "1":
            return qs.order_by("-boost", "?")
        return qs.order_by("-boost", "-created_at")

    def retrieve(self, request, *args, **kwargs):
        obj = self.get_object()
        Short.objects.filter(pk=obj.pk).update(views=obj.views + 1)
        obj.views += 1
        return Response(self.get_serializer(obj).data)

    @action(detail=True, methods=["post"], url_path="view", permission_classes=[])
    def register_view(self, request, pk=None):
        """Lentada video haqiqatan ko'rila boshlaganda chaqiriladi.

        `retrieve` faqat `/shorts/{id}/` deep-link'da ishlaydi — lenta esa
        ro'yxat endpoint'idan yuklanadi, ya'ni scroll qilib ko'rilgan
        videolarning `views` i oshmay qolardi. Frontend har slot faol
        bo'lganda (sessiyada bir marta) shu endpoint'ni chaqiradi.

        Anonim foydalanuvchilar ham hisoblanadi — bu global statistika.
        """
        obj = self.get_object()
        from apps.billing.limits import enforce_or_response
        limited = enforce_or_response(request, "shorts", obj.pk)
        if limited is not None:
            return limited
        Short.objects.filter(pk=obj.pk).update(views=F("views") + 1)
        return Response({"views": obj.views + 1})

    @action(detail=True, methods=["post"], url_path="react",
            permission_classes=[IsAuthenticated])
    def react(self, request, pk=None):
        """Like/dislike — HAR USER 1 MARTA (server tomonda cheklangan).

        Body: `{"reaction": "like"|"dislike"}` — bosilgan tugma. Server toggle
        qiladi (bir xil qayta bosilsa olib tashlanadi, boshqasi bosilsa
        almashadi). Kirish shart (anonim like qo'ya olmaydi).

        Javob: `{likes, dislikes, my_reaction}`.
        """
        from .models import ShortReaction
        from .reactions import toggle_reaction
        obj = self.get_object()
        clicked = (request.data.get("reaction") or "").strip()
        try:
            result = toggle_reaction(
                target=obj, target_model=Short, reaction_model=ShortReaction,
                fk_name="short", user=request.user, clicked=clicked,
            )
        except ValueError:
            return Response(
                {"detail": "reaction 'like' yoki 'dislike' bo'lishi kerak"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(result)


    # --- Shikoyat / Savol xatolik xabari ---------------------------------
    @action(detail=False, methods=["get"], url_path="report-reasons",
            permission_classes=[])
    def report_reasons(self, request):
        """Frontend modal uchun sabab ro'yxati (kalit + label)."""
        return Response([
            {"key": key, "label": label}
            for key, label in ShortReport.Reason.choices
        ])

    @action(detail=True, methods=["post"], url_path="report",
            permission_classes=[IsAuthenticated])
    def report(self, request, pk=None):
        """Video haqida shikoyat yuborish (bitta user — bitta video — 1 marta).

        Body: `{ "reason": "sexual|violent|...", "text"?: "..." }`
        Javob: `{ ok: true }` yoki 409 (allaqachon yuborilgan).
        """
        short = self.get_object()
        if ShortReport.objects.filter(user=request.user, short=short).exists():
            return Response(
                {"detail": "Bu videoga allaqachon shikoyat yuborgansiz."},
                status=status.HTTP_409_CONFLICT,
            )
        serializer = ShortReportWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ShortReport.objects.create(
            user=request.user, short=short,
            reason=serializer.validated_data["reason"],
            text=serializer.validated_data.get("text", ""),
        )
        return Response({"ok": True}, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="question-feedback",
            permission_classes=[IsAuthenticated])
    def question_feedback(self, request, pk=None):
        """Savol xato tuzilganligi haqida xabar (bitta user — bitta video — 1 marta).

        Body: `{ "text": "1-savol xato tuzilgan, ..." }`
        """
        short = self.get_object()
        if ShortQuestionFeedback.objects.filter(user=request.user, short=short).exists():
            return Response(
                {"detail": "Bu video savollari haqida allaqachon xabar yuborgansiz."},
                status=status.HTTP_409_CONFLICT,
            )
        serializer = ShortQuestionFeedbackWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ShortQuestionFeedback.objects.create(
            user=request.user, short=short,
            text=serializer.validated_data["text"],
        )
        return Response({"ok": True}, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"], url_path="my-feedback",
            permission_classes=[IsAuthenticated])
    def my_feedback(self, request, pk=None):
        """Foydalanuvchi ushbu video uchun allaqachon nima yuborganini qaytaradi.

        Frontend "Shikoyat" / "Savol xato" tugmalarini disable qilishi uchun.
        """
        from .models import ShortReaction
        from .reactions import my_reaction
        short = self.get_object()
        return Response({
            "reported": ShortReport.objects
                .filter(user=request.user, short=short).exists(),
            "question_reported": ShortQuestionFeedback.objects
                .filter(user=request.user, short=short).exists(),
            "my_reaction": my_reaction(
                target=short, reaction_model=ShortReaction,
                fk_name="short", user=request.user,
            ),
        })


    @action(detail=True, methods=["post"], url_path="mark-dead",
            permission_classes=[IsAuthenticated])
    def mark_dead(self, request, pk=None):
        """Frontend player YouTube xatoligini olganda chaqiradi.

        MUHIM XAVFSIZLIK: foydalanuvchi so'rovi O'ZI is_dead=True qilib
        qo'ymaydi. Aks holda oddiy user Postman orqali istalgan videoni
        "o'lik" deb belgilay olardi. Server O'ZI YouTube (oEmbed) ni chaqirib
        videoning haqiqatan mavjud emasligini TASDIQLAYDI — faqat shunda
        `is_dead=True` yoziladi. Boshqa hollarda faqat report_count oshadi.

        Talablar:
          - auth qilingan bo'lish (anonim spam'ning oldi olinadi)
          - server oEmbed 401/404 qaytarsa — video haqiqatan yo'q, tasdiq
          - oEmbed 200 → video ochiq, so'rov qabul qilinmaydi
        """
        from django.utils import timezone
        from . import mock_data
        try:
            short = Short.objects.get(pk=pk)
        except Short.DoesNotExist:
            return Response({"detail": "topilmadi"}, status=status.HTTP_404_NOT_FOUND)

        # Har bir report'ni hisobga olamiz — admin panelida ko'rinadi.
        Short.objects.filter(pk=short.pk).update(
            dead_reported_at=timezone.now(),
            dead_report_count=short.dead_report_count + 1,
        )

        # DeadVideoReport yozuvi — bitta foydalanuvchi bitta short'ga faqat
        # bir marta shikoyat yozadi (yangilanadi holos).
        yt_id = short.youtube_id or mock_data.extract_youtube_id(short.youtube_link) or ""
        report, _created = DeadVideoReport.objects.get_or_create(
            user=request.user, short=short,
            defaults={
                "youtube_url": short.youtube_link,
                "youtube_id": yt_id,
            },
        )
        # Har chaqiruvda `updated_at` yangilanadi (fresh signal admin uchun).
        report.youtube_url = short.youtube_link
        report.youtube_id = yt_id

        # Server tomonidan tasdiqlash.
        if short.is_dead:
            report.verified = True
            report.verify_result = DeadVideoReport.VerifyResult.DEAD
            report.verify_detail = "Allaqachon o'lik deb belgilangan"
            report.save()
            return Response({"ok": True, "dead": True, "verified": True})

        alive, reason = _verify_youtube_alive(short.youtube_link)
        if alive is True:
            report.verified = False
            report.verify_result = DeadVideoReport.VerifyResult.ALIVE
            report.verify_detail = "oEmbed 200 — video mavjud"
            report.save()
            return Response({
                "ok": True, "dead": False, "verified": False,
                "detail": "YouTube video hozircha mavjud — shikoyat qabul qilindi.",
            })
        if alive is None:
            report.verified = False
            report.verify_result = DeadVideoReport.VerifyResult.ERROR
            reason_txt = reason or "noma'lum"
            report.verify_detail = ("Tekshirib bo'lmadi: " + reason_txt)[:200]
            report.save()
            return Response({
                "ok": True, "dead": False, "verified": False,
                "detail": f"Tekshirib bo'lmadi: {reason}. Admin qo'lda ko'radi.",
            })
        # `alive is False` — server tasdiqladi, video haqiqatan yo'q.
        report.verified = True
        report.verify_result = DeadVideoReport.VerifyResult.DEAD
        report.verify_detail = reason or "oEmbed 404/401"
        report.save()
        Short.objects.filter(pk=short.pk).update(is_dead=True)
        return Response({"ok": True, "dead": True, "verified": True})


def _verify_youtube_alive(youtube_link: str):
    """YouTube video haqiqatan mavjudligini tekshiradi.

    Ikki bosqichli tekshiruv, chunki oEmbed ba'zan noto'g'ri qaytaradi
    (masalan region-blocked yoki soft-deleted video 200 qaytishi mumkin,
    embed-blocked video esa 401 emas 200 qaytarishi mumkin):

      1. **yt-dlp** (asosiy) — meta ma'lumot yuklaydi. Xato "Video unavailable",
         "private", "removed" desa → o'lik. Muvaffaqiyat bo'lsa → tirik.
      2. **oEmbed** (fallback, yt-dlp bo'lmasa yoki xato bersa) — 401/404 → o'lik.

    Qaytish qiymatlari:
        (True,  reason)    — video mavjud (tasdiqlangan)
        (False, reason)    — video yo'q (tasdiqlangan)
        (None,  reason)    — tekshirib bo'lmadi (tarmoq/timeout/rate limit)
    """
    if not youtube_link:
        return (False, "URL bo'sh")

    # 1) yt-dlp — YouTube'ning haqiqiy holatini eng aniq beradi.
    try:
        import yt_dlp
        opts = {
            "quiet": True, "no_warnings": True,
            "skip_download": True, "noplaylist": True,
            "socket_timeout": 8,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(youtube_link, download=False)
        if info is None:
            return (False, "yt-dlp: video info yo'q")
        return (True, "yt-dlp OK")
    except ImportError:
        pass  # yt-dlp o'rnatilmagan — oEmbed'ga o'tamiz
    except Exception as exc:
        msg = str(exc).lower()
        dead_signals = (
            "video unavailable", "private video", "video is private",
            "has been removed", "no longer available",
            "removed by the uploader", "removed by the user",
            "this video is not available", "video has been terminated",
            "sign in to confirm your age",
        )
        if any(s in msg for s in dead_signals):
            return (False, f"yt-dlp: {str(exc)[:180]}")
        # Boshqa xato (network / rate limit) — oEmbed bilan qayta urinamiz.

    # 2) Fallback: oEmbed.
    import urllib.parse
    import urllib.request
    import urllib.error
    url = (
        "https://www.youtube.com/oembed?"
        + urllib.parse.urlencode({"url": youtube_link, "format": "json"})
    )
    req = urllib.request.Request(url, headers={
        "User-Agent": "listening.uz-verifier/1.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            return (resp.status == 200, "oEmbed 200")
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 404, 410):
            return (False, f"oEmbed HTTP {exc.code}")
        return (None, f"oEmbed HTTP {exc.code}")
    except Exception as exc:
        return (None, str(exc)[:200])


# --- IELTS Listening tests API --------------------------------------------


def _normalize_answer(v) -> str:
    """Foydalanuvchi javobi va admin variantini bir xil formatga keltiradi:
    lowercase + tashqi bo'sh joy trim + koʻp bo'shliqni bittaga tushiradi +
    trailing tinish belgilarini olib tashlaydi. IELTS'da odatda case va
    tinish e'tibordan tashqari."""
    if v is None:
        return ""
    s = str(v).strip().lower()
    # Tinish belgilarini olib tashlaymiz (radio/checkbox A/B/C harf javoblari
    # ham lowercase bo'ladi — admin panelida `A, a` yozishga hojat yo'q).
    import re
    s = re.sub(r"[^\w\s'-]+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


class IeltsListeningTestViewSet(viewsets.ReadOnlyModelViewSet):
    """IELTS Listening tayyor testlar — chop etilganlari.

    - `GET /api/ielts-tests/` — ro'yxat (HTML yo'q, faqat meta)
    - `GET /api/ielts-tests/{slug}/` — batafsil (HTML iframe uchun)
    - `POST /api/ielts-tests/{slug}/submit/` — foydalanuvchi topshirgan javoblar
      solishtiriladi va natija qaytariladi (auth talab qilinadi).
    """

    lookup_field = "slug"
    lookup_value_regex = r"[-a-zA-Z0-9_]+"
    pagination_class = StandardPageNumberPagination

    def get_queryset(self):
        qs = IeltsListeningTest.objects.filter(
            is_published=True,
        ).exclude(html="").order_by("-created_at")  # eng yangisi doim tepada

        # Qidiruv — sarlavha bo'yicha (ro'yxat sahifasidagi qidiruv maydoni).
        search = (self.request.query_params.get("search") or "").strip()
        if search:
            qs = qs.filter(title__icontains=search)

        # "Bajarilgan / bajarilmagan" filtri — foydalanuvchi natijalariga qarab.
        # `?done=1` faqat topshirilganlar, `?done=0` faqat topshirilmaganlar,
        # berilmasa hammasi (default). Anonim uchun "done" bo'sh — filtr yo'q.
        done = self.request.query_params.get("done")
        user = getattr(self.request, "user", None)
        if done in ("0", "1") and user and user.is_authenticated:
            done_ids = IeltsListeningTestResult.objects.filter(
                user=user,
            ).values_list("test_id", flat=True)
            qs = qs.filter(pk__in=done_ids) if done == "1" else qs.exclude(pk__in=done_ids)
        return qs

    def get_serializer_class(self):
        if self.action == "retrieve":
            return IeltsListeningTestDetailSerializer
        return IeltsListeningTestListSerializer

    def _my_results_map(self, tests):
        """`{test_id: IeltsListeningTestResult}` — joriy foydalanuvchi uchun.
        Ro'yxat/detail serializer "bajarilgan" belgisi va oldingi natija uchun."""
        user = getattr(self.request, "user", None)
        if not user or not user.is_authenticated:
            return {}
        ids = [t.id for t in tests] if isinstance(tests, (list, tuple)) else [tests.id]
        rows = IeltsListeningTestResult.objects.filter(user=user, test_id__in=ids)
        return {r.test_id: r for r in rows}

    def list(self, request, *args, **kwargs):
        page = self.paginate_queryset(self.filter_queryset(self.get_queryset()))
        objs = page if page is not None else list(self.get_queryset())
        ctx = {**self.get_serializer_context(), "my_results": self._my_results_map(objs)}
        ser = self.get_serializer(objs, many=True, context=ctx)
        if page is not None:
            return self.get_paginated_response(ser.data)
        return Response(ser.data)

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        # Kunlik IELTS limiti — kontent OCHILGANDA (mobil/dictation bilan bir xil).
        # `enforce_or_response` idempotent: bugun ochilgan testni qayta ochsa
        # o'tkazadi, limit tugagach YANGI testni bloklaydi (403 → LimitGate modal).
        from apps.billing.limits import enforce_or_response
        limited = enforce_or_response(request, "ielts", instance.pk)
        if limited is not None:
            return limited
        IeltsListeningTest.objects.filter(pk=instance.pk).update(views=instance.views + 1)
        instance.views += 1
        ctx = {**self.get_serializer_context(), "my_results": self._my_results_map(instance)}
        return Response(self.get_serializer(instance, context=ctx).data)

    @action(detail=True, methods=["post"], url_path="submit",
            permission_classes=[IsAuthenticated])
    def submit(self, request, slug=None):
        """Foydalanuvchi javoblarini tekshiradi.

        Body: `{"answers": {"1": "Monday", "2": ["A"], ...}}`
        Return: `{"score": 32, "total": 40, "results": {"1": true, "2": false, ...}}`

        Har savolga admin bergan ro'yxatdan (`answers[q]`) biror element
        foydalanuvchi javobiga to'g'ri kelsa — to'g'ri hisoblanadi. Multi-choice
        (masalan `["A","C"]`) uchun foydalanuvchi ham ro'yxat yuborsa har
        elementi normalize qilinib tekshiriladi.
        """
        test = self.get_object()
        given = request.data.get("answers") or {}
        if not isinstance(given, dict):
            return Response(
                {"detail": "`answers` obyekt bo'lishi kerak: {\"1\": \"...\", ...}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        correct_map = test.answers or {}
        results: dict[str, bool] = {}
        score = 0
        total = int(test.total_questions or 0)
        for q in range(1, total + 1):
            variants_raw = correct_map.get(str(q)) or correct_map.get(q) or []
            if isinstance(variants_raw, str):
                variants_raw = [variants_raw]
            variants = {_normalize_answer(v) for v in variants_raw if _normalize_answer(v)}
            user_raw = given.get(str(q)) or given.get(q)
            if user_raw is None:
                results[str(q)] = False
                continue
            if isinstance(user_raw, list):
                user_norms = {_normalize_answer(v) for v in user_raw if _normalize_answer(v)}
                # Multi-choice: hech qanday xato tanlov bo'lmasin va admin bergan
                # variantlar to'plami foydalanuvchi to'plamiga to'g'ri kelsin.
                ok = bool(variants) and user_norms == variants
            else:
                ok = _normalize_answer(user_raw) in variants and bool(variants)
            results[str(q)] = ok
            if ok:
                score += 1

        # Natijani PROFILGA saqlaymiz — har (user, test) uchun BITTA qator
        # (qayta topshirsa yangilanadi). Test sahifasiga qaytilganda ko'rsatiladi
        # va ro'yxatda "bajarilgan" belgisi shu yozuvdan chiqadi.
        if request.user and request.user.is_authenticated:
            IeltsListeningTestResult.objects.update_or_create(
                user=request.user, test=test,
                defaults={"score": score, "total": total, "results_json": results},
            )
        return Response({
            "score": score,
            "total": total,
            "results": results,
        })


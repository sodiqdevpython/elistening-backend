"""Shaxsiy lug'at — diktant matnida "bilmayman" deb belgilangan so'zlar.

    GET    /api/me/vocab/          → {"count", "results": [...]} (eng yangisi birinchi)
    POST   /api/me/vocab/          {"word", "context"?, "dictation"?: slug} → 201 | 200 (bor edi)
    DELETE /api/me/vocab/<id>/     → 204 (adashib bosilganini qaytarish)
    GET    /api/me/vocab/words/    → {"words": {"peacock": 12, ...}} — tugatish ekrani
                                     qaysi so'z allaqachon lug'atda ekanini bilsin

Hozircha faqat yig'iladi va profilda ko'rinadi. So'z bir foydalanuvchida bir
marta (`word` — kichik harf, chetdagi tinish belgilarisiz). Raqam lug'atga
kirmaydi — kamida bitta harf bo'lishi shart.
"""
from __future__ import annotations

import re

from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import Dictation, VocabWord

MAX_WORDS_PER_USER = 5000
LIST_LIMIT = 500
_EDGES = re.compile(r"^[\W_]+|[\W_]+$")
_LETTER = re.compile(r"[^\W\d_]")


def clean_word(raw: str) -> str:
    """Matndagi ko'rinishi: "City." → "City", "(Peacocks)" → "Peacocks"."""
    return _EDGES.sub("", (raw or "").strip().replace("’", "'").replace("‘", "'"))


class VocabWordSerializer(serializers.ModelSerializer):
    dictation = serializers.SerializerMethodField()

    class Meta:
        model = VocabWord
        fields = ("id", "word", "display", "context", "dictation", "created_at")

    def get_dictation(self, obj):
        d = obj.dictation
        if d is None:
            return None
        from .serializers import DictationListSerializer
        return {"slug": d.slug, "title": d.title,
                "type_slug": DictationListSerializer().get_type_slug(d)}


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def my_vocab(request):
    if request.method == "GET":
        qs = VocabWord.objects.filter(user=request.user).select_related("dictation")
        return Response({
            "count": qs.count(),
            "results": VocabWordSerializer(qs[:LIST_LIMIT], many=True).data,
        })

    display = clean_word(str(request.data.get("word") or ""))[:64]
    if not _LETTER.search(display):
        return Response({"error": {"code": "not_a_word", "message": "Bu so'z emas."}},
                        status=status.HTTP_400_BAD_REQUEST)
    word = display.lower()
    existing = VocabWord.objects.filter(user=request.user, word=word).first()
    if existing:
        return Response(VocabWordSerializer(existing).data, status=status.HTTP_200_OK)
    if VocabWord.objects.filter(user=request.user).count() >= MAX_WORDS_PER_USER:
        return Response({"error": {"code": "vocab_full",
                                   "message": f"Lug'atda {MAX_WORDS_PER_USER} ta so'z — chegara."}},
                        status=status.HTTP_400_BAD_REQUEST)
    slug = str(request.data.get("dictation") or "")
    dictation = Dictation.objects.filter(slug=slug).only("id").first() if slug else None
    item, created = VocabWord.objects.get_or_create(
        user=request.user, word=word,
        defaults={"display": display, "dictation": dictation,
                  "context": " ".join(str(request.data.get("context") or "").split())[:400]},
    )
    return Response(VocabWordSerializer(item).data,
                    status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def my_vocab_item(request, pk: int):
    deleted, _ = VocabWord.objects.filter(user=request.user, pk=pk).delete()
    return Response(status=status.HTTP_204_NO_CONTENT if deleted else status.HTTP_404_NOT_FOUND)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def my_vocab_words(request):
    rows = VocabWord.objects.filter(user=request.user).values_list("word", "id")
    return Response({"words": dict(rows)})

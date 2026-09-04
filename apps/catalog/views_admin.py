"""Diktant uchun qo'lda audio segmentlash editori (admin only).

Admin `Dictation` yozuvini ochib, audio yuklaydi va **🎬 Segment editor**
tugmasini bosadi. Waveform ustida drag qilib har gapning boshi/oxirini
belgilaydi va matnini yozadi. Ctrl+S bilan saqlanadi.

Natija — `Dictation.body` JSON maydoniga yoziladi:

    [
      {"start_ms": 0,     "end_ms": 3800,  "text": "Hello there."},
      {"start_ms": 3800,  "end_ms": 8100,  "text": "How are you?"},
    ]

Diktant `is_published=True` ga aylanadi va sayt darrov ko'radi.
"""
import json

from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .models import Dictation


@staff_member_required
def segment_editor(request, dictation_id: int) -> HttpResponse:
    """Waveform + segmentlar ro'yxati bilan interfeys."""
    dictation = get_object_or_404(Dictation, pk=dictation_id)
    # `body` allaqachon list of {start_ms, end_ms, text} — to'g'ridan-to'g'ri.
    segments = list(dictation.body or [])
    return render(request, "admin/catalog/dictation/segment_editor.html", {
        "dictation": dictation,
        "segments_json": json.dumps(segments, ensure_ascii=False),
        "audio_url": dictation.audio.url if dictation.audio else None,
        "back_url": reverse("admin:catalog_dictation_change", args=[dictation.pk]),
        "site_header": "eListening.uz admin",
    })


@staff_member_required
@require_POST
def segment_editor_save(request, dictation_id: int) -> JsonResponse:
    """`Dictation.body` ni to'liq almashtirish."""
    dictation = get_object_or_404(Dictation, pk=dictation_id)
    try:
        data = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        return JsonResponse({"ok": False, "error": f"JSON xato: {exc}"}, status=400)

    segments_data = data.get("segments", [])
    # Faqat matni bor va vaqti to'g'ri bo'lgan segmentlarni olamiz.
    clean = []
    for row in segments_data:
        text = (row.get("text") or "").strip()
        try:
            start_ms = int(row.get("start_ms") or 0)
            end_ms = int(row.get("end_ms") or 0)
        except (TypeError, ValueError):
            continue
        if not text or end_ms <= start_ms:
            continue
        clean.append({"start_ms": start_ms, "end_ms": end_ms, "text": text})

    # Vaqt bo'yicha saralash (foydalanuvchi tartibsiz kiritgan bo'lishi mumkin).
    clean.sort(key=lambda r: r["start_ms"])

    dictation.body = clean
    if clean:
        dictation.is_published = True
    dictation.save(update_fields=["body", "is_published", "updated_at"])

    return JsonResponse({
        "ok": True,
        "saved_count": len(clean),
        "is_published": dictation.is_published,
        "message": f"{len(clean)} ta segment saqlandi",
    })

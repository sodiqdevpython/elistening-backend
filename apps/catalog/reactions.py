"""Like/dislike — server tomonda HAR USER 1 MARTA cheklangan.

Shorts va Dictation uchun umumiy mantiq. Frontend qaysi tugma bosilganini
(`like` yoki `dislike`) yuboradi; server foydalanuvchining oldingi reaksiyasini
o'qib, TOGGLE qiladi (YouTube kabi):

  - Reaksiya yo'q + `like`      → like qo'yiladi
  - `like` bor + `like`         → olib tashlanadi (toggle off)
  - `like` bor + `dislike`      → dislike'ga almashadi
  - `dislike` bor + `like`      → like'ga almashadi

Hisoblagichlar (`likes`/`dislikes`) atomik `F()` bilan yangilanadi.
"""
from __future__ import annotations

from django.db import transaction
from django.db.models import F

from .models import ReactionValue


def toggle_reaction(*, target, target_model, reaction_model, fk_name: str,
                    user, clicked: str) -> dict:
    """Bitta reaksiyani qo'llaydi va yangi holatni qaytaradi.

    `clicked` — foydalanuvchi bosgan tugma: "like" yoki "dislike".
    Qaytadi: `{"likes": int, "dislikes": int, "my_reaction": "like"|"dislike"|None}`.
    """
    if clicked not in (ReactionValue.LIKE, ReactionValue.DISLIKE):
        raise ValueError("reaction must be 'like' or 'dislike'")

    with transaction.atomic():
        existing = (reaction_model.objects
                    .select_for_update()
                    .filter(user=user, **{fk_name: target})
                    .first())
        prev = existing.value if existing else None
        # Toggle: bir xil tugma qayta bosilsa — olib tashlanadi.
        new = None if prev == clicked else clicked

        d_like = 0
        d_dislike = 0
        if prev == ReactionValue.LIKE:
            d_like -= 1
        elif prev == ReactionValue.DISLIKE:
            d_dislike -= 1
        if new == ReactionValue.LIKE:
            d_like += 1
        elif new == ReactionValue.DISLIKE:
            d_dislike += 1

        if d_like or d_dislike:
            target_model.objects.filter(pk=target.pk).update(
                likes=F("likes") + d_like,
                dislikes=F("dislikes") + d_dislike,
            )

        if new is None:
            if existing:
                existing.delete()
        elif existing:
            existing.value = new
            existing.save(update_fields=["value", "updated_at"])
        else:
            reaction_model.objects.create(user=user, value=new, **{fk_name: target})

    target.refresh_from_db(fields=["likes", "dislikes"])
    return {
        "likes": target.likes,
        "dislikes": target.dislikes,
        "my_reaction": new,
    }


def my_reaction(*, target, reaction_model, fk_name: str, user) -> str | None:
    """Foydalanuvchining shu target'dagi joriy reaksiyasi (yoki None)."""
    if not user or not user.is_authenticated:
        return None
    row = (reaction_model.objects
           .filter(user=user, **{fk_name: target})
           .values_list("value", flat=True)
           .first())
    return row

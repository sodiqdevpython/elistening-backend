"""Keng (16:9) `Short` yozuvlarini `Dictation` (video) ga ko'chiradi.

**Nega kerak.** Film, Multfilm va Yangilik ilgari HAR DOIM `Short` jadvaliga
tushardi, `Short` esa sayt va ilovada TIK Shorts shablonida ochiladi.
Natijada katalogdan "Filmlar" ga qo'shilgan oddiy YouTube videosi vertikal
lentada, o'ziga umuman mos kelmaydigan ko'rinishda chiqardi — foydalanuvchi
buni "na video na shorts" deb ta'rifladi.

Qoida endi bitta: **havola** modelni tanlaydi
(`models.is_shorts_url`) — `youtube.com/shorts/...` → `Short`, qolgani →
`Dictation`. Bu komanda o'sha qoidani MAVJUD yozuvlarga qo'llaydi.

Ko'chirilganda hamma narsa saqlanadi: sarlavha, havola, transkript
(`full_text`, `words_json`), savollar, davomiylik, ko'rishlar, like/dislike
va chop etilgan holati. AI qayta ishlatilmaydi — token sarflanmaydi.

    python manage.py move_landscape_shorts            # nima ko'chishini ko'rsatadi
    python manage.py move_landscape_shorts --apply    # bazaga yozadi

`--keep` bilan eski `Short` o'chirilmaydi (faqat `is_published=False`
qilinadi) — tekshirib bo'lgach qo'lda o'chirsangiz bo'ladi.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.catalog.models import Dictation, Short, is_shorts_url

# `Short.content_type` → `Dictation.type`. Ikkalasida ham bir xil ma'no.
TYPE_MAP = {
    Short.ContentType.MOVIE: Dictation.Type.MOVIE,
    Short.ContentType.CARTOON: Dictation.Type.CARTOON,
    Short.ContentType.NEWS: Dictation.Type.NEWS,
    Short.ContentType.SHORT: Dictation.Type.RANDOM_VIDEO,
}


def _already_moved(short: Short) -> bool:
    """Shu havola bilan Dictation allaqachon bormi (qayta ishga tushirish)."""
    return Dictation.objects.filter(youtube_link=short.youtube_link).exists()


class Command(BaseCommand):
    help = "Keng (16:9) Short yozuvlarini Dictation (video) ga ko'chiradi."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply", action="store_true",
            help="Bazaga yozadi. Berilmasa faqat hisobot (dry-run).",
        )
        parser.add_argument(
            "--keep", action="store_true",
            help="Eski Short o'chirilmaydi, faqat chop etishdan olinadi.",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        keep = options["keep"]

        moved = skipped = 0
        for short in Short.objects.all().iterator():
            # Havola tik bo'lsa — joyida qoladi.
            if is_shorts_url(short.youtube_link):
                continue
            if _already_moved(short):
                skipped += 1
                self.stdout.write(f"  SKIP  Short #{short.pk} - Dictation allaqachon bor")
                continue

            new_type = TYPE_MAP.get(short.content_type, Dictation.Type.RANDOM_VIDEO)
            self.stdout.write(
                f"  Short #{short.pk} ({short.content_type}) -> Dictation "
                f"({new_type}) — {short.title[:60]}"
            )
            moved += 1
            if not apply_changes:
                continue

            with transaction.atomic():
                Dictation.objects.create(
                    title=short.title or "YouTube video",
                    type=new_type,
                    cefr_level=short.cefr_from or "",
                    is_media=True,
                    youtube_link=short.youtube_link,
                    full_text=short.full_text or "",
                    words_json=short.words_json or [],
                    mcq_questions=short.mcq_questions or [],
                    tfng_questions=short.tfng_questions or [],
                    fill_gap_questions=short.fill_gap_questions or [],
                    tests_status="done" if (short.mcq_questions or []) else "idle",
                    audio_duration_sec=short.duration_sec or 0,
                    views=short.views,
                    likes=short.likes,
                    dislikes=short.dislikes,
                    is_published=short.is_published,
                )
                if keep:
                    Short.objects.filter(pk=short.pk).update(is_published=False)
                else:
                    short.delete()

        if apply_changes:
            self.stdout.write(self.style.SUCCESS(
                f"Tayyor — {moved} ta video ko'chirildi"
                + (f", {skipped} ta tashlab ketildi." if skipped else "."),
            ))
        else:
            self.stdout.write(self.style.WARNING(
                f"Dry-run: {moved} ta ko'chadi"
                + (f", {skipped} ta tashlab ketiladi." if skipped else ".")
                + " Yozish uchun --apply qo'shing.",
            ))

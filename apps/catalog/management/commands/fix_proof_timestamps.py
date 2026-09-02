"""Mavjud yozuvlardagi `proof_from_text` timestamp'larini haqiqiy vaqtga moslaydi.

Claude iqtibosning har bo'lagiga to'g'ri `[t]` qo'ymaydi — ko'pincha iqtibos
boshlangan qatorning vaqtini keyingi bo'laklarga ham nusxalaydi. Xato **doim
bir tomonga**: belgilangan vaqt haqiqiydan ertaroq (o'lchandi: +2 s dan +11 s
gacha). Natijada "savol joyi" indikatori va "Isbot" tugmasi noto'g'ri joyni
ko'rsatardi.

Yangi kontent uchun bu `shorts_pipeline.align_proof_timestamps()` da avtomatik
bajariladi. Bu buyruq esa ESKI yozuvlarni tuzatadi.

    python manage.py fix_proof_timestamps            # faqat ko'rsatadi
    python manage.py fix_proof_timestamps --apply    # yozadi
"""
from django.core.management.base import BaseCommand

from apps.catalog.models import Dictation, Short
from apps.catalog.shorts_pipeline import align_proof_timestamps


class Command(BaseCommand):
    help = "proof_from_text timestamp'larini words_json bo'yicha tuzatadi"

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply", action="store_true",
            help="O'zgarishlarni bazaga yozadi (aks holda faqat ko'rsatadi).",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        total_fixed = 0
        total_rows = 0

        for model, label in ((Short, "Short"), (Dictation, "Dictation")):
            for obj in model.objects.exclude(words_json=[]).iterator():
                quiz = {
                    "multiple_choice_questions": obj.mcq_questions or [],
                    "tfng_questions": obj.tfng_questions or [],
                    "fill_gap_questions": obj.fill_gap_questions or [],
                }
                if not any(quiz.values()):
                    continue
                fixed = align_proof_timestamps(quiz, obj.words_json or [])
                if not fixed:
                    continue
                total_fixed += fixed
                total_rows += 1
                self.stdout.write(f"{label} #{obj.pk}: {fixed} ta timestamp — {obj.title[:50]}")
                if apply_changes:
                    obj.mcq_questions = quiz["multiple_choice_questions"]
                    obj.tfng_questions = quiz["tfng_questions"]
                    obj.fill_gap_questions = quiz["fill_gap_questions"]
                    obj.save(update_fields=[
                        "mcq_questions", "tfng_questions", "fill_gap_questions", "updated_at",
                    ])

        verb = "tuzatildi" if apply_changes else "tuzatilishi kerak"
        self.stdout.write(self.style.SUCCESS(
            f"\n{total_rows} ta yozuvda {total_fixed} ta timestamp {verb}."
        ))
        if not apply_changes and total_fixed:
            self.stdout.write("Yozish uchun: --apply")

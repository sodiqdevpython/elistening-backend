"""Mavjud savollarni video bo'yicha ketma-ketlikka soladi.

Server endi AI natijasini saqlashdan oldin majburan tartibga soladi
(`shorts_pipeline._order_quiz`), lekin undan OLDIN yaratilgan Short va
Dictation yozuvlari eski holatda qoladi. Ikkita muammo bor edi:

1. **Ro'yxat ichida** isbot vaqtlari chalkash — masalan MCQ isbotlari
   `[32.6, 136.7, 5.8, 88.7, 101.7]`.
2. **Ro'yxatlar orasida** — har bo'lim videoni boshidan oxirigacha alohida
   bosib o'tardi, ya'ni 2-savol 90-soniyada, 3-savol esa 8-soniyada bo'lardi.
   Foydalanuvchi buni "javoblar 3, 1, 2, 4 tartibda kelayabdi" deb ko'rardi.

Komanda ikkalasini ham tuzatadi: ro'yxatlarni saralaydi va har savolga
xronologik `number` qo'yadi. AI'ga umuman murojaat qilmaydi — token
sarflanmaydi.

    python manage.py fix_question_order            # nima o'zgarishini ko'rsatadi
    python manage.py fix_question_order --apply    # bazaga yozadi
"""
from django.core.management.base import BaseCommand

from apps.catalog.models import Dictation, Short
from apps.catalog.shorts_pipeline import _order_quiz, sequence_is_chronological

FIELDS = ("mcq_questions", "tfng_questions", "fill_gap_questions")

# Model maydonlari ↔ quiz kalitlari.
_QUIZ_KEYS = {
    "mcq_questions": "multiple_choice_questions",
    "tfng_questions": "tfng_questions",
    "fill_gap_questions": "fill_gap_questions",
}


def _to_quiz(obj) -> dict:
    return {
        quiz_key: list(getattr(obj, field, None) or [])
        for field, quiz_key in _QUIZ_KEYS.items()
    }


class Command(BaseCommand):
    help = "Short va Dictation savollarini video bo'yicha ketma-ketlikka soladi."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply", action="store_true",
            help="Bazaga yozadi. Berilmasa faqat hisobot chiqadi (dry-run).",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        total_fixed = 0

        for model, label in ((Short, "Short"), (Dictation, "Dictation")):
            fixed = 0
            for obj in model.objects.all().iterator():
                before = _to_quiz(obj)
                if not any(before.values()):
                    continue
                was_chronological = sequence_is_chronological(before)
                after = _order_quiz(_to_quiz(obj))

                changed = [
                    field for field, quiz_key in _QUIZ_KEYS.items()
                    if after[quiz_key] != (getattr(obj, field, None) or [])
                ]
                if not changed:
                    continue

                fixed += 1
                why = "tartib" if not was_chronological else "raqamlash"
                self.stdout.write(f"  {label} #{obj.pk} — {why}: {', '.join(changed)}")
                if apply_changes:
                    for field, quiz_key in _QUIZ_KEYS.items():
                        setattr(obj, field, after[quiz_key])
                    obj.save(update_fields=[*FIELDS, "updated_at"])
            total_fixed += fixed
            self.stdout.write(f"{label}: {fixed} ta yozuv tuzatildi.")

        if apply_changes:
            self.stdout.write(self.style.SUCCESS(
                f"Tayyor — {total_fixed} ta yozuv tartibga solindi.",
            ))
        else:
            self.stdout.write(self.style.WARNING(
                f"Dry-run: {total_fixed} ta yozuv o'zgaradi. "
                f"Yozish uchun --apply qo'shing.",
            ))

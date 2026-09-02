"""Mavjud savollarni isbot vaqti bo'yicha qayta saralaydi.

Server endi AI natijasini saqlashdan oldin majburan saralaydi
(`shorts_pipeline._order_quiz`), lekin undan OLDIN yaratilgan Short va
Dictation yozuvlarida savollar hali ham chalkash tartibda yotibdi —
masalan MCQ isbotlari `[32.6, 136.7, 5.8, 88.7, 101.7]`.

Bu komanda AI'ga umuman murojaat qilmaydi (token sarflanmaydi) — faqat
bazadagi ro'yxatlarni qayta tartiblaydi.

    python manage.py fix_question_order            # nima o'zgarishini ko'rsatadi
    python manage.py fix_question_order --apply    # bazaga yozadi
"""
from django.core.management.base import BaseCommand

from apps.catalog.models import Dictation, Short
from apps.catalog.shorts_pipeline import _proof_seconds, _sort_by_proof

FIELDS = ("mcq_questions", "tfng_questions", "fill_gap_questions")


def _timestamps(questions):
    return [_proof_seconds(q) for q in (questions or []) if isinstance(q, dict)]


def _is_ordered(questions) -> bool:
    """Ro'yxatdagi timestamp'lar o'sish tartibidami (isbotsizlar hisobga olinmaydi)."""
    secs = [s for s in _timestamps(questions) if s is not None]
    return secs == sorted(secs)


class Command(BaseCommand):
    help = "Short va Dictation savollarini isbot vaqti bo'yicha qayta saralaydi."

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
                changed = []
                for field in FIELDS:
                    questions = getattr(obj, field, None)
                    if not questions or _is_ordered(questions):
                        continue
                    setattr(obj, field, _sort_by_proof(questions))
                    changed.append(field)
                if not changed:
                    continue
                fixed += 1
                self.stdout.write(
                    f"  {label} #{obj.pk} — {', '.join(changed)}"
                )
                if apply_changes:
                    obj.save(update_fields=[*changed, "updated_at"])
            total_fixed += fixed
            self.stdout.write(f"{label}: {fixed} ta yozuvda tartib buzilgan edi.")

        if apply_changes:
            self.stdout.write(self.style.SUCCESS(
                f"Tayyor — {total_fixed} ta yozuv saralandi.",
            ))
        else:
            self.stdout.write(self.style.WARNING(
                f"Dry-run: {total_fixed} ta yozuv o'zgaradi. "
                f"Yozish uchun --apply qo'shing.",
            ))

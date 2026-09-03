"""IELTS test sahifalarini JORIY shablon bilan qayta yasaydi.

**Nega kerak:** `IeltsListeningTest.html` bazada parse paytida qotib qoladi.
Plyerga yangi imkoniyat qo'shilsa (masalan audio pozitsiyasini surish paneli)
ilgari parse qilingan testlar undan bexabar qoladi — foydalanuvchi
"audio boshlash bor, lekin xohlagan joyiga surish yo'q" degan holat aynan shu.

`parts_json` bor testlar **tarmoqqa chiqmasdan** qayta yasaladi. Eski
yozuvlarda u bo'lmasa manba sahifa bir marta qayta olinadi va `parts_json`
to'ldiriladi — keyingi safar tarmoq kerak bo'lmaydi.

Savollar, javoblar va natijalar TEGILMAYDI.

    python manage.py rebuild_ielts_html            # nima o'zgarishini ko'rsatadi
    python manage.py rebuild_ielts_html --apply    # bazaga yozadi
    python manage.py rebuild_ielts_html --apply --id 3 --id 7
"""
from django.core.management.base import BaseCommand

from apps.catalog.ielts_parser import IeltsParseError, rebuild_html
from apps.catalog.models import IeltsListeningTest

#: Shablonda shu belgi bo'lsa — sahifa yangi plyer bilan yasalgan.
MARKER = "audio-seek"


class Command(BaseCommand):
    help = "IELTS test HTML sahifalarini joriy shablon bilan qayta yasaydi"

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true",
                            help="Bazaga yozadi (aks holda faqat ko'rsatadi)")
        parser.add_argument("--id", type=int, action="append", default=[],
                            help="Faqat shu ID(lar) — bir necha marta berish mumkin")
        parser.add_argument("--all", action="store_true",
                            help="Yangi plyer allaqachon bor bo'lganlarni ham qayta yasaydi")

    def handle(self, *args, **options):
        qs = IeltsListeningTest.objects.all().order_by("id")
        if options["id"]:
            qs = qs.filter(id__in=options["id"])

        apply_changes = options["apply"]
        rebuilt = skipped = failed = 0

        for test in qs:
            if not options["all"] and MARKER in (test.html or ""):
                skipped += 1
                self.stdout.write(f"  #{test.id} {test.slug} — allaqachon yangi, o'tkazildi")
                continue

            if not apply_changes:
                self.stdout.write(f"  #{test.id} {test.slug} — qayta yasaladi")
                rebuilt += 1
                continue

            try:
                # Qaysi manbadan foydalanilganini `rebuild_html` o'zi aytadi
                # (parts_json / saqlangan sahifa / manba sahifa) — oldindan
                # taxmin qilish noto'g'ri natija ko'rsatardi.
                source = rebuild_html(test)
                if source:
                    rebuilt += 1
                    self.stdout.write(self.style.SUCCESS(
                        f"  #{test.id} {test.slug} — yangilandi ({source})"
                    ))
                else:
                    failed += 1
                    self.stdout.write(self.style.WARNING(
                        f"  #{test.id} {test.slug} — bo'sh natija, tegilmadi"
                    ))
            except IeltsParseError as exc:
                failed += 1
                self.stdout.write(self.style.ERROR(f"  #{test.id} {test.slug} — {exc}"))
            except Exception as exc:  # tarmoq, timeout va h.k.
                failed += 1
                self.stdout.write(self.style.ERROR(f"  #{test.id} {test.slug} — {exc}"))

        verb = "yangilandi" if apply_changes else "yangilanadi"
        self.stdout.write(self.style.SUCCESS(
            f"\n{rebuilt} ta {verb}, {skipped} ta o'tkazildi, {failed} ta xato"
        ))
        if not apply_changes and rebuilt:
            self.stdout.write("Bazaga yozish uchun: --apply")

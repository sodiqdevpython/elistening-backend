"""Diktant audiolarini doimiy bitreytga (CBR) o'tkazadi — bo'laklar aniq yangrasin.

    python manage.py make_audio_cbr                 # nechtasi VBR — faqat ko'rsatadi
    python manage.py make_audio_cbr --apply         # o'tkazadi
    python manage.py make_audio_cbr --apply --jobs 4 --only-imported

Serverda:  docker compose exec web python manage.py make_audio_cbr --apply

Sabab va o'lchovlar — `apps/catalog/audio_cbr.py`. Allaqachon CBR bo'lganlar
o'tkazib yuboriladi, ya'ni buyruqni necha marta ishga tushirsangiz ham xavfsiz.
AI (Whisper/Haiku) chaqirilmaydi.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from django.core.management.base import BaseCommand

from apps.catalog.audio_cbr import AudioConvertError, convert_to_cbr, needs_cbr, store_cbr
from apps.catalog.models import Dictation


class Command(BaseCommand):
    help = "VBR/ABR MP3 diktant audiolarini CBR'ga o'tkazadi (aniq bo'lak ijrosi uchun)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Haqiqatda o'tkazish (aks holda faqat hisob).")
        parser.add_argument("--jobs", type=int, default=2, help="Bir vaqtda nechta ffmpeg (default 2).")
        parser.add_argument("--only-imported", action="store_true", help="Faqat tashqi manbadan olinganlar.")

    def handle(self, *args, **opts):
        qs = Dictation.objects.exclude(audio="").exclude(audio__isnull=True).only("id", "audio", "title")
        if opts["only_imported"]:
            qs = qs.exclude(source_url="")
        todo = []
        for d in qs.iterator():
            try:
                path = d.audio.path
            except Exception:
                continue
            if needs_cbr(path):
                todo.append(d)
        self.stdout.write(f"Audioli diktantlar: {qs.count()} · VBR/ABR (o'tkaziladi): {len(todo)}")
        if not opts["apply"] or not todo:
            if todo and not opts["apply"]:
                self.stdout.write("Haqiqatda o'tkazish uchun: --apply")
            return

        total, done, failed = len(todo), 0, []
        t0 = time.time()
        width = len(str(total))
        # ffmpeg — oqimlarda (og'ir qism); bazaga yozish — shu oqimda, ya'ni
        # oqimlar ma'lumotlar bazasiga umuman ulanmaydi.
        with ThreadPoolExecutor(max_workers=max(1, opts["jobs"])) as pool:
            futures = {pool.submit(convert_to_cbr, d.audio.path): d for d in todo}
            for fut in as_completed(futures):
                d = futures[fut]
                done += 1
                try:
                    store_cbr(d, Path(d.audio.path), fut.result())
                    mark = "+"
                except (AudioConvertError, OSError) as e:
                    failed.append((d, str(e)))
                    mark = "x"
                elapsed = time.time() - t0
                eta = int(elapsed / done * (total - done))
                self.stdout.write(f"[{done:>{width}}/{total} {done / total * 100:5.1f}% | qoldi ~{eta // 60}:{eta % 60:02d}] "
                                  f"{mark} {d.title[:60]}")
        self.stdout.write(self.style.SUCCESS(f"Tayyor: {total - len(failed)} ta o'tkazildi."))
        for d, err in failed:
            self.stdout.write(self.style.ERROR(f"  XATO #{d.pk} {d.title[:50]}: {err}"))

"""AI Job worker — DB-based durable navbat.

Ish oqimi (foydalanuvchi ko'zi bilan):
  1. Admin `/admin/catalog/short/add/` yoki dictation'ga YouTube link kiritib
     saqlaydi.
  2. Signal `post_save` AIJob(pending, step=whisper) yozadi va darrov qaytadi
     — admin browserda kutmaydi.
  3. Ish ORQA FONDA bajariladi:
       - PROD (REDIS_URL bor): signal transaction commit bo'lgach Celery task
         (`apps.catalog.tasks.process_ai_job`) jo'natadi. Celery worker bajaradi.
       - DEV (SQLite, redis yo'q): shu moduldagi THREAD (`run_forever`) DB'ni
         poll qilib bajaradi.
  4. Muvaffaqiyatli whisper — keyingi AIJob(step=haiku) navbatga qo'shiladi.
  5. Whisper + Haiku ikkalasi tayyor bo'lgach ⇒ user feed'da ko'rinadi.

Xato bo'lsa (kvota, tarmoq...) status=failed, `error` matnda. Admin qo'lda
qayta ishga tushirishi mumkin (bulk amal orqali).

CRASH-SAFE (VPS o'chib-yonsa): navbat manbasi — Postgres'dagi `AIJob` jadvali.
`running` bo'lib qolgan yozuvlar `_reap_stale()` orqali qayta `pending`'ga
o'tkaziladi va Celery Beat'ning `sweep_pending_jobs` taski ularni qayta
jo'natadi. Hech qanday ish yo'qolmaydi.
"""
from __future__ import annotations

import logging
import time
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

# Bir bosqich uchun eng ko'p urinish
MAX_ATTEMPTS = 3
# `running` job shu davomiyliktan uzoq turgan bo'lsa — crash deb qabul qilamiz
STALE_RUNNING_MIN = 30


# --- Navbatga qo'shish (durable AIJob yaratish) -----------------------------

def enqueue_whisper(kind: str, object_id: int):
    """Short yoki Dictation uchun Whisper bosqichini navbatga qo'shadi.
    `(job, created)` qaytaradi — chaqiruvchi `dispatch_job(job)` bilan
    Celery'ga jo'natadi (yoki dev'da thread o'zi oladi)."""
    from .models import AIJob
    return AIJob.objects.get_or_create(
        kind=kind, object_id=object_id, step=AIJob.Step.WHISPER,
        defaults={"status": AIJob.Status.PENDING},
    )


def enqueue_haiku(kind: str, object_id: int):
    from .models import AIJob
    return AIJob.objects.get_or_create(
        kind=kind, object_id=object_id, step=AIJob.Step.HAIKU,
        defaults={"status": AIJob.Status.PENDING},
    )


def enqueue_channel_ingest(object_id: int):
    """Kanal ingest task uchun AIJob yaratadi (unique per task pk)."""
    from .models import AIJob
    return AIJob.objects.get_or_create(
        kind=AIJob.Kind.CHANNEL, object_id=object_id, step=AIJob.Step.INGEST,
        defaults={"status": AIJob.Status.PENDING},
    )


def enqueue_ielts_parse(object_id: int):
    """IELTS Listening test uchun HTML parse ishini navbatga qo'shadi."""
    from .models import AIJob
    return AIJob.objects.get_or_create(
        kind=AIJob.Kind.IELTS, object_id=object_id, step=AIJob.Step.PARSE,
        defaults={"status": AIJob.Status.PENDING},
    )


# --- Dispatch: DB'dagi job'ni bajarilishga jo'natish ------------------------

def _queue_for(job) -> str:
    """Kanal-ingest og'ir ish — alohida `ingest` navbatida."""
    from .models import AIJob
    return "ingest" if job.kind == AIJob.Kind.CHANNEL else "default"


def dispatch_job(job) -> None:
    """Job'ni bajarilishga jo'natadi.

    - PROD (USE_CELERY): transaction commit bo'lgach Celery task jo'natiladi
      (`on_commit` — AIJob commit bo'lmasdan worker uni o'qib "topilmadi"
      demasligi uchun).
    - DEV: hech nima qilmaymiz — thread poll qilib o'zi oladi.
    """
    if not getattr(settings, "USE_CELERY", False):
        return
    from .tasks import process_ai_job
    job_id = job.id
    queue = _queue_for(job)
    transaction.on_commit(
        lambda: process_ai_job.apply_async((job_id,), queue=queue)
    )


def _reap_stale() -> int:
    """Server crash bo'lganda `running` bo'lib qolgan yozuvlarni tiklaydi."""
    from .models import AIJob
    cutoff = timezone.now() - timedelta(minutes=STALE_RUNNING_MIN)
    return (AIJob.objects
            .filter(status=AIJob.Status.RUNNING, started_at__lt=cutoff)
            .update(status=AIJob.Status.PENDING, started_at=None,
                    error="Server ishga qayta tushdi — qayta ishga qo'shildi"))


def _claim_next():
    """Keyingi pending job'ni atomic ravishda `running`'ga o'tkazadi va
    qaytaradi. Yo'q bo'lsa None. Boshqa worker/thread bilan poyga bo'lmaydi
    (SELECT FOR UPDATE)."""
    from .models import AIJob
    with transaction.atomic():
        job = (AIJob.objects
               .select_for_update(skip_locked=True)
               .filter(status=AIJob.Status.PENDING)
               .order_by("created_at")
               .first())
        if not job:
            return None
        job.status = AIJob.Status.RUNNING
        job.started_at = timezone.now()
        job.attempts += 1
        job.error = ""
        job.save(update_fields=["status", "started_at", "attempts", "error", "updated_at"])
        return job


def _finish_ok(job) -> None:
    from .models import AIJob
    job.status = AIJob.Status.DONE
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "finished_at", "updated_at"])


def _finish_fail(job, message: str) -> None:
    from .models import AIJob
    job.status = AIJob.Status.FAILED
    job.error = (message or "")[:2000]
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "error", "finished_at", "updated_at"])


def _run_short_whisper(object_id: int) -> None:
    from .models import Short
    from .shorts_pipeline import generate_short
    short = Short.objects.get(pk=object_id)
    # `generate_short` allaqachon Whisper + Haiku ikkalasini bajaradi
    # (chunki Short pipeline'i shu yo'l bilan qurilgan). Shu bois Haiku
    # bosqichini AI navbat orqali qayta chaqirmaymiz — belgilaymiz done.
    generate_short(short)


def _run_dictation_whisper(object_id: int) -> None:
    from .models import Dictation
    from .transcribe import transcribe_dictation
    dictation = Dictation.objects.get(pk=object_id)
    transcribe_dictation(dictation)


def _run_dictation_haiku(object_id: int) -> None:
    from .models import Dictation
    from .shorts_pipeline import generate_dictation_tests
    dictation = Dictation.objects.get(pk=object_id)
    generate_dictation_tests(dictation)


def _run_ielts_parse(object_id: int) -> None:
    from django.utils import timezone
    from .ielts_parser import IeltsParseError, parse_test
    from .models import IeltsListeningTest
    test = IeltsListeningTest.objects.get(pk=object_id)
    test.status = IeltsListeningTest.Status.PARSING
    test.parse_error = ""
    test.save(update_fields=["status", "parse_error", "updated_at"])
    try:
        result = parse_test(test.source_url)
    except IeltsParseError as exc:
        test.status = IeltsListeningTest.Status.FAILED
        test.parse_error = str(exc)[:2000]
        test.save(update_fields=["status", "parse_error", "updated_at"])
        return
    test.html = result["html"]
    test.total_questions = result["total_questions"]
    # Xom partlar ham saqlanadi — plyer yaxshilanganda sahifani manba saytga
    # qayta murojaat qilmasdan yangilash uchun (`ielts_parser.rebuild_html`).
    test.parts_json = result.get("parts") or []
    # Sarlavha bo'sh bo'lsa parser natijasidan olamiz — admin qo'lda o'zgartirsa
    # `title` bo'sh qolmagan bo'ladi, uni buzmaymiz.
    if not (test.title or "").strip():
        test.title = (result["title"] or "IELTS Listening Test")[:250]
    test.status = IeltsListeningTest.Status.PARSED
    test.parse_error = ""
    test.save(update_fields=[
        "html", "parts_json", "total_questions", "title", "status", "parse_error",
        "updated_at",
    ])


def _run_channel_ingest(object_id: int) -> None:
    from .channel_ingest import ChannelIngestError, run_ingest
    from .models import ChannelIngestTask
    task = ChannelIngestTask.objects.get(pk=object_id)
    try:
        run_ingest(task)
    except ChannelIngestError as exc:
        # Task o'z holatini `failed` ga o'tkazamiz — admin darrov ko'radi.
        from django.utils import timezone
        task.status = ChannelIngestTask.Status.FAILED
        task.error = str(exc)[:2000]
        task.finished_at = timezone.now()
        task.save(update_fields=["status", "error", "finished_at", "updated_at"])
        # AIJob failed bo'lmasin — task o'z holatida xato bor.
        return


def _claim_specific(job_id: int):
    """Berilgan job'ni FAQAT `pending` bo'lsa `running`'ga o'tkazadi va
    qaytaradi. Boshqa worker allaqachon olgan (running/done) bo'lsa None —
    ikki worker bitta ishni ikki marta bajarmaydi."""
    from .models import AIJob
    with transaction.atomic():
        job = (AIJob.objects
               .select_for_update(skip_locked=True)
               .filter(pk=job_id, status=AIJob.Status.PENDING)
               .first())
        if not job:
            return None
        job.status = AIJob.Status.RUNNING
        job.started_at = timezone.now()
        job.attempts += 1
        job.error = ""
        job.save(update_fields=["status", "started_at", "attempts", "error", "updated_at"])
        return job


def _execute_job(job) -> None:
    """Claim qilingan job'ni bajaradi (whisper/haiku/ingest) va yakunlaydi.
    Xato bo'lsa MAX_ATTEMPTS gacha qayta `pending` qiladi. Dictation whisper
    tugagach Haiku bosqichini navbatga qo'yadi va (celery rejimida) jo'natadi."""
    from .models import AIJob
    try:
        if job.kind == AIJob.Kind.SHORT and job.step == AIJob.Step.WHISPER:
            # Short pipeline'i whisper+haiku ikkalasini bir marta qiladi.
            _run_short_whisper(job.object_id)
            _finish_ok(job)
        elif job.kind == AIJob.Kind.DICTATION and job.step == AIJob.Step.WHISPER:
            _run_dictation_whisper(job.object_id)
            _finish_ok(job)
            # Whisper tayyor — Haiku'ni navbatga qo'yamiz va jo'natamiz.
            haiku_job, _ = enqueue_haiku(AIJob.Kind.DICTATION, job.object_id)
            dispatch_job(haiku_job)
        elif job.kind == AIJob.Kind.DICTATION and job.step == AIJob.Step.HAIKU:
            _run_dictation_haiku(job.object_id)
            _finish_ok(job)
        elif job.kind == AIJob.Kind.CHANNEL and job.step == AIJob.Step.INGEST:
            _run_channel_ingest(job.object_id)
            _finish_ok(job)
        elif job.kind == AIJob.Kind.IELTS and job.step == AIJob.Step.PARSE:
            _run_ielts_parse(job.object_id)
            _finish_ok(job)
        else:
            _finish_fail(job, f"Noma'lum job turi: {job.kind}/{job.step}")
    except Exception as exc:
        logger.exception("AI job failed: %s", job)
        # MAX_ATTEMPTS gacha qayta urinamiz — pending'ga qaytaramiz (thread
        # yoki keyingi beat-sweep qayta oladi).
        if job.attempts < MAX_ATTEMPTS:
            AIJob.objects.filter(pk=job.pk).update(
                status=AIJob.Status.PENDING, error=str(exc)[:2000],
                started_at=None,
            )
        else:
            _finish_fail(job, str(exc))


def run_job_by_id(job_id: int) -> None:
    """Celery task shu funksiyani chaqiradi — berilgan job'ni claim + execute.
    Idempotent: job allaqachon olingan/tugagan bo'lsa jimgina qaytadi."""
    _reap_stale()
    job = _claim_specific(job_id)
    if job is None:
        return
    _execute_job(job)


def run_pending_once() -> bool:
    """Navbatdan BITTA job olib bajaradi (dev thread ishlatadi).
    `True` — bajarildi, `False` — navbat bo'sh."""
    _reap_stale()
    job = _claim_next()
    if not job:
        return False
    _execute_job(job)
    return True


def reap_and_dispatch() -> int:
    """Beat-sweeper: crash-recovery + kechikkan/yo'qolgan xabarlar.

    1) `running` bo'lib qotib qolganlarni `pending`'ga qaytaradi.
    2) BARCHA `pending` job'larni topib qayta Celery'ga jo'natadi.

    VPS o'chib-yonganda ham shu funksiya (Beat orqali) navbatni tiklaydi —
    hech nima yo'qolmaydi. `dispatch_job` `on_commit` ishlatgani sabab, bu
    yerda ochiq transaction yo'q (autocommit), shu bois darrov jo'natiladi.
    """
    from .models import AIJob
    reaped = _reap_stale()
    dispatched = 0
    pending = AIJob.objects.filter(status=AIJob.Status.PENDING).only("id", "kind")
    for job in pending:
        dispatch_job(job)
        dispatched += 1
    if reaped or dispatched:
        logger.info("sweep: reaped=%s dispatched=%s", reaped, dispatched)
    return dispatched


def run_forever(poll_sec: float = 5.0) -> None:
    """Cheksiz worker halqasi — DEV thread (SQLite, redis yo'q) uchun."""
    logger.info("AI worker THREAD started (poll=%.1fs)", poll_sec)
    while True:
        try:
            worked = run_pending_once()
        except Exception:
            logger.exception("worker loop error")
            worked = False
        if not worked:
            time.sleep(poll_sec)

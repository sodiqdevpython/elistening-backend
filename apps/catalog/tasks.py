"""Celery tasklari — AI navbatini orqa fonda bajaradi.

Navbat manbasi DB'dagi `AIJob` (durable). Tasklar shunchaki "shu job'ni
bajar" deb chaqiradi; barcha holat DB'da saqlanadi. Shu bois VPS o'chib-yonsa
ham `sweep_pending_jobs` (Celery Beat, har 20s) qolgan ishlarni qayta jo'natadi
— hech nima yo'qolmaydi.

DEV rejimida (REDIS_URL yo'q) bu tasklar CHAQIRILMAYDI — `apps/catalog` ichidagi
thread worker DB'ni poll qilib o'zi bajaradi. `dispatch_job` shuni hal qiladi.
"""
from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    name="apps.catalog.tasks.process_ai_job",
    bind=True, ignore_result=True, acks_late=True,
    max_retries=0,   # qayta urinish AIJob.attempts orqali boshqariladi
)
def process_ai_job(self, job_id: int) -> None:
    """Bitta AIJob'ni bajaradi (whisper / haiku / ingest).

    Navbat `dispatch_job` orqali tanlanadi: kanal-ingest job'lari `ingest`
    navbatiga (worker-ingest), qolganlari `default` navbatiga (worker) tushadi.
    """
    from .ai_worker import run_job_by_id
    run_job_by_id(job_id)


@shared_task(
    name="apps.catalog.tasks.sweep_pending_jobs",
    ignore_result=True,
)
def sweep_pending_jobs() -> int:
    """Beat har 20s da chaqiradi: stale'larni tiklaydi va pending job'larni
    qayta jo'natadi. Crash-recovery mexanizmi shu."""
    from .ai_worker import reap_and_dispatch
    return reap_and_dispatch()

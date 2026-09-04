"""Admin Short/Dictation/ChannelIngestTask saqlaganda AI ishini navbatga
qo'shadi. `post_save` da `AIJob` yaratiladi va (prod'da) Celery'ga jo'natiladi.

Idempotent: `enqueue_*` `get_or_create` ishlatadi — o'zgarishsiz qayta saqlash
dublikat ish rejalashtirmaydi. `dispatch_job` `on_commit` ishlatgani sabab,
Celery task FAQAT tranzaksiya commit bo'lgach jo'natiladi — worker AIJob'ni
commit bo'lmasdan o'qib "topilmadi" demaydi.
"""
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import ChannelIngestTask, Dictation, IeltsListeningTest, Short


@receiver(post_save, sender=Short)
def enqueue_short_ai(sender, instance: Short, created: bool, **kwargs):
    """YouTube link berilgan Short saqlangach — AI navbatga qo'shiladi.

    Faqat transkript hali yo'q (idle yoki failed) va link mavjud bo'lsa.
    `is_dead` bo'lsa umuman ishlamaymiz.
    """
    if not instance.youtube_link or instance.is_dead:
        return
    if instance.transcription_status == Short.TranscriptionStatus.DONE:
        return
    if instance.transcription_status == Short.TranscriptionStatus.PROCESSING:
        # Ish allaqachon ketmoqda; navbatda dublikat qilmaymiz
        return
    from .ai_worker import dispatch_job, enqueue_whisper
    from .models import AIJob
    job, _ = enqueue_whisper(AIJob.Kind.SHORT, instance.pk)
    dispatch_job(job)


@receiver(post_save, sender=Dictation)
def enqueue_dictation_ai(sender, instance: Dictation, created: bool, **kwargs):
    """Dictation admin saqlangach: agar YouTube havolasi yoki audio bor
    bo'lsa Whisper navbatga qo'shiladi. Whisper tayyor bo'lgach Haiku ham
    (worker o'zi ulaydi)."""
    has_source = bool(instance.youtube_link) or bool(getattr(instance, "audio", None))
    if not has_source:
        return
    from .ai_worker import dispatch_job, enqueue_haiku, enqueue_whisper
    from .models import AIJob
    if instance.transcription_status == "done":
        # Whisper tayyor — savollar hali yo'q bo'lsa Haiku qo'yamiz
        needs_tests = not (instance.mcq_questions or instance.tfng_questions
                           or instance.fill_gap_questions)
        if needs_tests and instance.tests_status != "processing":
            job, _ = enqueue_haiku(AIJob.Kind.DICTATION, instance.pk)
            dispatch_job(job)
        return
    if instance.transcription_status == "processing":
        return
    job, _ = enqueue_whisper(AIJob.Kind.DICTATION, instance.pk)
    dispatch_job(job)


@receiver(post_save, sender=IeltsListeningTest)
def enqueue_ielts_parse_task(sender, instance: IeltsListeningTest, created: bool, **kwargs):
    """Yangi (yoki HTML hali bo'sh) IELTS testda parser'ni ishga tushiramiz.

    Faqat:
      - `pending` yoki `failed` bo'lsa (idle holatlar) va
      - HTML hali bo'sh bo'lsa
    Aks holda admin qo'lda javob kiritayotgan bo'lishi mumkin — biror
    o'zgartirishda parser qayta ishga tushib javoblar yo'qolib ketmasin.
    """
    if instance.html:
        return
    if instance.status not in (
        IeltsListeningTest.Status.PENDING,
        IeltsListeningTest.Status.FAILED,
    ):
        return
    from .ai_worker import dispatch_job, enqueue_ielts_parse
    job, _ = enqueue_ielts_parse(instance.pk)
    dispatch_job(job)


@receiver(post_save, sender=ChannelIngestTask)
def enqueue_channel_ingest_task(sender, instance: ChannelIngestTask, created: bool, **kwargs):
    """Yangi ChannelIngestTask yaratilishi bilanoq AIJob'ga yozamiz.

    Faqat `pending` holatida navbatga qo'shamiz — admin qayta saqlagach
    (masalan xatoni ko'rib `pending` ga qaytargach) qayta ishga tushadi.
    """
    if instance.status != instance.Status.PENDING:
        return
    from .ai_worker import dispatch_job, enqueue_channel_ingest
    job, _ = enqueue_channel_ingest(instance.pk)
    dispatch_job(job)


# MUHIM: Admin diktantni PROXY bo'lim orqali (masalan "Yangilik (video)" =
# NewsDictation) saqlasa, `post_save` `sender` PROXY klass bo'ladi —
# `sender=Dictation` receiver ISHLAMAYDI. Shu bois har proxy uchun ham
# `enqueue_dictation_ai` ni ulaymiz. Aks holda proxy orqali qo'shilgan
# yangilikda whisper/haiku avtomatik ishga tushmasdi (savollar chiqmasdi).
from .models import DICTATION_PROXIES, SHORT_PROXIES  # noqa: E402

for _proxy in DICTATION_PROXIES:
    post_save.connect(enqueue_dictation_ai, sender=_proxy,
                      dispatch_uid=f"enqueue_dictation_ai_{_proxy.__name__}")

# Short proxy'lari (hozircha faqat `ShortVideo`) uchun ham xuddi shu.
for _proxy in SHORT_PROXIES:
    post_save.connect(enqueue_short_ai, sender=_proxy,
                      dispatch_uid=f"enqueue_short_ai_{_proxy.__name__}")

"""Barcha ilovalar uchun bazaviy model va tanlovlar."""
from django.db import models


class CEFR(models.TextChoices):
    A1 = "A1", "A1"
    A2 = "A2", "A2"
    B1 = "B1", "B1"
    B2 = "B2", "B2"
    C1 = "C1", "C1"
    C2 = "C2", "C2"


CEFR_ORDER = ["A1", "A2", "B1", "B2", "C1", "C2"]

# Daraja oshirish uchun taxminiy tinglash soati (profil progress bari uchun).
LEVEL_HOURS_REQUIRED = {"A1": 15, "A2": 25, "B1": 40, "B2": 60, "C1": 85, "C2": 120}


def next_cefr(level: str) -> str:
    """Berilgan darajadan keyingi daraja (C2 dan keyin ham C2)."""
    try:
        idx = CEFR_ORDER.index(level)
    except ValueError:
        return "A2"
    return CEFR_ORDER[min(idx + 1, len(CEFR_ORDER) - 1)]


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField("yaratilgan", auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField("yangilangan", auto_now=True)

    class Meta:
        abstract = True


class OrderedModel(TimeStampedModel):
    order = models.PositiveIntegerField("tartib", default=0, db_index=True)
    is_active = models.BooleanField("faol", default=True)

    class Meta:
        abstract = True
        ordering = ["order", "id"]


class SiteConfig(TimeStampedModel):
    """Sayt bo'yicha yagona (singleton) sozlama — admin bir marta to'ldiradi.

    Hozircha faqat "Bog'lanish" uchun Telegram username saqlaydi: navbar'dagi
    "Bog'lanish" menyusi `t.me/<username>` ga olib boradi.
    """

    contact_telegram = models.CharField(
        "Bog'lanish uchun Telegram username", max_length=64, blank=True, default="",
        help_text="@ SIZ ye — faqat username. Masalan: sodiq2005 → t.me/sodiq2005. "
                  "Bo'sh qoldirilsa 'Bog'lanish' menyusi ko'rinmaydi.",
    )

    class Meta:
        verbose_name = "Sayt sozlamasi"
        verbose_name_plural = "Sayt sozlamalari"

    def __str__(self):
        return "Sayt sozlamalari"

    def save(self, *args, **kwargs):
        # Faqat bitta yozuv bo'lsin (singleton) — pk doim 1.
        self.pk = 1
        # @ va to'liq havolani tozalab, faqat username qoldiramiz.
        u = (self.contact_telegram or "").strip()
        u = u.replace("https://t.me/", "").replace("http://t.me/", "")
        u = u.replace("t.me/", "").lstrip("@").strip().strip("/")
        self.contact_telegram = u
        super().save(*args, **kwargs)
        # Admin o'zgartirsa — kesh darrov yangilansin (aks holda 5 daqiqa eski
        # qiymat qolardi va "Bog'lanish" kech chiqardi).
        from django.core.cache import cache
        cache.delete(cls_cache_key())

    @classmethod
    def get_solo(cls) -> "SiteConfig":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


def cls_cache_key() -> str:
    return "site_config_v1"


class AppAd(TimeStampedModel):
    """Mobil ilova ochilganda chiqadigan reklama (rasm/gif + matn).

    `GET /api/app-ad/` eng oxirgi FAOL reklamani beradi. Ilova har cold-start'da
    (ilk ochilishda) uni ko'rsatadi. Faqat mobil ilova uchun.
    """

    is_active = models.BooleanField(
        "Faol", default=False,
        help_text="Yoqilsa ilova ochilganda shu reklama chiqadi. Bir vaqtda "
                  "eng oxirgi faol reklama ko'rsatiladi.",
    )
    image = models.ImageField(
        "Rasm yoki GIF", upload_to="ads/", null=True, blank=True,
        help_text="PNG / JPG / GIF. Modal tepasida ko'rsatiladi.",
    )
    title = models.CharField("Sarlavha", max_length=200, blank=True, default="")
    body = models.TextField(
        "Matn", blank=True, default="",
        help_text="Ixtiyoriy. Ichidagi havolalar (https://...) ilovada bosiladigan bo'ladi.",
    )
    link_url = models.URLField(
        "Bosilganda ochiladigan havola", blank=True, default="",
        help_text="Bo'sh bo'lmasa — rasm/tugma bosilганда shu havola ochiladi.",
    )
    duration_sec = models.PositiveIntegerField(
        "Avto-yopilish (soniya)", default=0,
        help_text="0 — foydalanuvchi X ni bosmaguncha turadi. Masalan 5 — 5 soniyadan keyin yopiladi.",
    )

    class Meta:
        verbose_name = "Ilova reklamasi"
        verbose_name_plural = "Ilova reklamalari"
        ordering = ["-created_at"]

    def __str__(self):
        return self.title or f"Reklama #{self.pk}"

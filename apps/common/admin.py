"""Admin uchun umumiy yordamchilar."""
from django.contrib import admin
from django.utils.html import format_html

from .models import AppAd, SiteConfig


class BaseModelAdmin(admin.ModelAdmin):
    """Barcha admin sinflari uchun umumiy sozlamalar.

    * har sahifada 25 ta yozuv (pagination)
    * yaratilgan/yangilangan maydonlari faqat o'qish uchun
    * yuqorida ham, pastda ham saqlash tugmalari
    """

    list_per_page = 25
    save_on_top = True
    readonly_fields = ("created_at", "updated_at")

    def get_readonly_fields(self, request, obj=None):
        """`readonly_fields` dan MAVJUD BO'LMAGAN nomlarni olib tashlaydi.

        Sabab: bazadagi `created_at`/`updated_at` hamma modelda ham yo'q
        (`TimeStampedModel` dan meros olmaganlarida), ular esa bu yerda
        default sifatida turadi.

        **Diqqat:** faqat model maydonlarini qoldirish YETARLI EMAS. Django
        `readonly_fields` ga admin metodini ham (masalan `AppAdAdmin.preview`
        — rasm ko'rinishi) qo'yishga ruxsat beradi. Ilgari shu filtr uni
        tashlab yuborar, keyin `get_form` uni formadan CHIQARMAS va
        "Unknown field(s) (preview)" `FieldError` bilan admin sahifasi
        umuman ochilmasdi. Shu bois admin/model atributlari ham qoladi.
        """
        model_fields = {f.name for f in self.model._meta.get_fields()}

        def exists(name: str) -> bool:
            return name in model_fields or hasattr(self, name) or hasattr(self.model, name)

        return tuple(f for f in self.readonly_fields if exists(f))


@admin.register(SiteConfig)
class SiteConfigAdmin(admin.ModelAdmin):
    """Yagona sozlama — faqat bitta yozuv. Qo'shish/o'chirish o'chirilgan."""

    list_display = ("__str__", "contact_telegram", "updated_at")
    readonly_fields = ("created_at", "updated_at")

    def has_add_permission(self, request):
        # Singleton — allaqachon yozuv bo'lsa qo'shishni bloklaymiz.
        return not SiteConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        # To'g'ridan-to'g'ri bitta (pk=1) yozuv tahririga yo'naltiramiz.
        from django.shortcuts import redirect
        obj = SiteConfig.get_solo()
        return redirect("admin:common_siteconfig_change", object_id=obj.pk)


@admin.register(AppAd)
class AppAdAdmin(BaseModelAdmin):
    """Ilova VA sayt reklamalari — rasm/gif + matn, faol bittasi ko'rsatiladi.

    Ikkita rasm: `image` (mobil, tik) va `image_web` (sayt, keng). Qolgan
    hamma narsa — sarlavha, matn, havola, avto-yopilish — bir xil.
    Sayt rasmi bo'sh qoldirilsa mobil rasm ishlatiladi.
    """

    list_display = ("__str__", "is_active", "preview", "preview_web", "duration_sec", "created_at")
    list_editable = ("is_active",)
    list_filter = ("is_active",)
    fieldsets = (
        (None, {"fields": ("is_active", "title", "body", "link_url", "duration_sec")}),
        ("Rasmlar", {
            "fields": ("image", "preview", "image_web", "preview_web"),
            "description": "Mobil rasm odatda TIK, sayt rasmi esa KENG bo'ladi. "
                           "Sayt rasmi bo'sh qoldirilsa mobil rasm ishlatiladi.",
        }),
        ("Xizmat", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )
    readonly_fields = ("preview", "preview_web", "created_at", "updated_at")

    @staticmethod
    def _thumb(field):
        if not field:
            return "—"
        return format_html('<img src="{}" style="max-height:120px;border-radius:8px" />', field.url)

    @admin.display(description="Mobil ko'rinishi")
    def preview(self, obj):
        return self._thumb(obj.image)

    @admin.display(description="Sayt ko'rinishi")
    def preview_web(self, obj):
        return self._thumb(obj.image_web)

"""Paynet admin — MOLIYAVIY yozuvlar, shu bois deyarli hammasi faqat o'qish.

Tranzaksiyani qo'lda tahrirlash yoki o'chirish umuman mumkin emas: u Paynet
reestri bilan solishtiriladi (kunlik sverka) va bir qator o'zgarsa ikki tomon
raqamlari farq qilib qoladi. Pulni qaytarish yo'li bitta — Paynet tomonidan
`CancelTransaction`.
"""
from django.contrib import admin

from .models import PaynetCredential, PaynetTransaction


@admin.register(PaynetTransaction)
class PaynetTransactionAdmin(admin.ModelAdmin):
    list_display = ("transaction_id", "user", "amount_label", "state", "performed_at", "cancelled_at")
    list_filter = ("state", "service_id", "performed_at")
    search_fields = ("transaction_id", "id", "user__username", "user__display_name", "user__telegram_id")
    date_hierarchy = "performed_at"
    list_select_related = ("user",)
    readonly_fields = [f.name for f in PaynetTransaction._meta.fields]

    @admin.display(description="Summa", ordering="amount_tiyin")
    def amount_label(self, obj):
        return f"{obj.amount_uzs:,}".replace(",", " ") + " so'm"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PaynetCredential)
class PaynetCredentialAdmin(admin.ModelAdmin):
    """Faqat "parol almashtirilganmi va qachon" — hash ko'rsatilmaydi."""

    list_display = ("__str__", "updated_at")
    readonly_fields = ("created_at", "updated_at")
    fields = ("created_at", "updated_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

"""Click admin — MOLIYAVIY yozuvlar, faqat o'qish.

Buyurtmani qo'lda tahrirlash yoki o'chirish mumkin emas: u Click reestri
bilan solishtiriladi va bir qator o'zgarsa ikki tomon raqamlari farq qilib
qoladi. Pulni qaytarish yo'li — Click tomonidan reversal.
"""
from django.contrib import admin

from .models import ClickOrder


@admin.register(ClickOrder)
class ClickOrderAdmin(admin.ModelAdmin):
    list_display = ("pk", "user", "plan", "months", "amount_label", "status",
                    "click_trans_id", "paid_at", "created_at")
    list_filter = ("status", "plan", "created_at")
    search_fields = ("id", "click_trans_id", "click_paydoc_id",
                     "user__username", "user__display_name", "user__telegram_id")
    date_hierarchy = "created_at"
    list_select_related = ("user", "plan")
    readonly_fields = [f.name for f in ClickOrder._meta.fields]

    @admin.display(description="Summa", ordering="amount_uzs")
    def amount_label(self, obj):
        return f"{int(obj.amount_uzs):,}".replace(",", " ") + " so'm"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

from django.contrib import admin

from apps.common.admin import BaseModelAdmin

from .models import BotMessage


@admin.register(BotMessage)
class BotMessageAdmin(BaseModelAdmin):
    list_display = ("telegram_id", "user", "kind", "short_text", "status", "sent_at", "created_at")
    list_filter = ("kind", "status", "created_at")
    search_fields = ("telegram_id", "text", "user__username", "user__display_name")
    autocomplete_fields = ("user",)
    list_select_related = ("user",)
    actions = ("action_requeue",)

    @admin.display(description="Matn")
    def short_text(self, obj):
        return obj.text[:60]

    @admin.action(description="Qayta navbatga qo'yish")
    def action_requeue(self, request, queryset):
        count = queryset.update(status=BotMessage.Status.PENDING, error="", sent_at=None)
        self.message_user(request, f"{count} ta xabar qayta navbatga qo'yildi.")

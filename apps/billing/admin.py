from django.contrib import admin

from apps.common.admin import BaseModelAdmin

from .models import InviteReward, Payment, Plan, Subscription, SubscriptionEvent


@admin.register(Plan)
class PlanAdmin(BaseModelAdmin):
    list_display = ("name_uz", "code", "price_uzs", "price_usd", "daily_lesson_limit",
                    "daily_exam_limit", "is_default", "order", "is_active")
    list_editable = ("order", "is_active", "is_default")
    list_filter = ("is_active", "is_default")
    search_fields = ("name_uz", "name_en", "code")
    prepopulated_fields = {"code": ("name_en",)}
    fieldsets = (
        (None, {"fields": ("code", "name_uz", "name_en", "order", "is_active", "is_default")}),
        ("Narx", {"fields": ("price_uzs", "price_usd")}),
        ("Imkoniyatlar", {"fields": ("features_uz", "features_en")}),
        ("Kvotalar", {"fields": ("daily_lesson_limit", "daily_exam_limit")}),
    )


@admin.register(Subscription)
class SubscriptionAdmin(BaseModelAdmin):
    list_display = ("user", "plan", "status", "reason", "started_at", "expires_at", "active_flag")
    list_filter = ("status", "plan", "reason", "started_at")
    search_fields = ("user__username", "user__display_name")
    autocomplete_fields = ("user", "plan")
    list_select_related = ("user", "plan")

    @admin.display(description="Faolmi", boolean=True)
    def active_flag(self, obj):
        return obj.is_active


@admin.register(Payment)
class PaymentAdmin(BaseModelAdmin):
    list_display = ("user", "plan", "provider", "status", "amount_uzs", "external_id", "created_at")
    list_filter = ("provider", "status", "created_at")
    search_fields = ("user__username", "external_id")
    autocomplete_fields = ("user", "plan")
    list_select_related = ("user", "plan")
    readonly_fields = ("created_at", "updated_at", "raw")


@admin.register(SubscriptionEvent)
class SubscriptionEventAdmin(BaseModelAdmin):
    """Tarif TARIXI — faqat o'qish uchun.

    Yozuvlar o'zgarmas bo'lishi kerak: profil sahifasidagi "qachon qaysi
    tarifni qanday oldim" shu jadvaldan chiqadi. Admin ularni tahrirlay
    olmaydi, aks holda tarix ishonchsiz bo'lib qolardi.
    """

    list_display = ("user", "plan", "reason", "months", "started_at", "expires_at", "created_at")
    list_filter = ("reason", "plan", "created_at")
    search_fields = ("user__username", "user__display_name", "note")
    autocomplete_fields = ("user", "plan")
    list_select_related = ("user", "plan")
    readonly_fields = ("user", "plan", "reason", "months", "started_at", "expires_at",
                       "note", "created_at", "updated_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(InviteReward)
class InviteRewardAdmin(BaseModelAdmin):
    """Taklif sovg'alari ledger'i — sarflangan takliflar. O'zgartirib bo'lmaydi.

    Bu jadval "kim nechta taklifni sovg'aga aylantirgan" ni saqlaydi va
    sovg'a hisobi to'g'ridan-to'g'ri shunga tayanadi. Qo'lda qator qo'shish
    yoki o'chirish foydalanuvchiga bepul tarif berib yuborardi.
    """

    list_display = ("user", "plan", "months", "invites_spent", "created_at")
    list_filter = ("plan", "created_at")
    search_fields = ("user__username", "user__display_name")
    autocomplete_fields = ("user", "plan")
    list_select_related = ("user", "plan")
    readonly_fields = ("user", "plan", "months", "invites_spent", "event",
                       "created_at", "updated_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.utils.html import format_html

from apps.common.admin import BaseModelAdmin

from .models import (
    ActiveSession, DailyActivity, Invitation, PendingInvite, TelegramOTP,
    TestAccountLogin, User,
)


class DailyActivityInline(admin.TabularInline):
    model = DailyActivity
    extra = 0
    fields = ("date", "seconds", "hours")
    readonly_fields = ("hours",)
    ordering = ("-date",)
    verbose_name = "Kunlik faollik"
    verbose_name_plural = "Kunlik faollik"


class DeletionRequestedFilter(admin.SimpleListFilter):
    """"Akkauntni o'chirish so'rovi bor/yo'q" bo'yicha filtr."""
    title = "O'chirish so'rovi"
    parameter_name = "deletion"

    def lookups(self, request, model_admin):
        return [("yes", "So'rov bor"), ("no", "Yo'q")]

    def queryset(self, request, qs):
        if self.value() == "yes":
            return qs.filter(deletion_requested_at__isnull=False)
        if self.value() == "no":
            return qs.filter(deletion_requested_at__isnull=True)
        return qs


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_per_page = 25
    save_on_top = True
    list_display = (
        "username", "display_name", "telegram_id", "cefr_level",
        "total_hours", "invited_count", "deletion_flag", "is_active", "date_joined",
    )
    list_filter = (DeletionRequestedFilter, "cefr_level", "language", "gender", "is_active", "is_staff", "date_joined")
    search_fields = ("username", "display_name", "telegram_username", "telegram_id", "email", "invite_code")
    ordering = ("-date_joined",)
    readonly_fields = ("last_login", "date_joined", "invite_code", "total_hours")
    inlines = [DailyActivityInline]
    autocomplete_fields = ("invited_by",)
    list_select_related = True

    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("Profil", {"fields": ("display_name", "avatar", "email", "cefr_level", "gender", "language")}),
        ("Telegram", {"fields": ("telegram_id", "telegram_username")}),
        ("Faollik", {"fields": ("last_active_at", "total_hours")}),
        ("Akkauntni o'chirish so'rovi", {
            "fields": ("deletion_requested_at",),
            "description": "Foydalanuvchi o'chirishni so'ragan. Akkaunt AVTOMATIK "
                           "o'chmaydi — o'zingiz hal qilasiz. Bekor qilish uchun "
                           "sanani bo'shatib saqlang (yoki user qayta login qilsa "
                           "o'zi bekor bo'ladi).",
        }),
        ("Taklif", {"fields": ("invite_code", "invited_by")}),
        ("Ruxsatlar", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Sanalar", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("username", "display_name", "telegram_id", "cefr_level", "password1", "password2"),
        }),
    )

    @admin.display(description="Umumiy soat", ordering="id")
    def total_hours(self, obj):
        return round(obj.total_seconds / 3600, 1)

    @admin.display(description="Takliflar")
    def invited_count(self, obj):
        return obj.invited_count

    @admin.display(description="O'chirish so'rovi", boolean=True)
    def deletion_flag(self, obj):
        return obj.deletion_requested_at is not None


@admin.register(DailyActivity)
class DailyActivityAdmin(BaseModelAdmin):
    list_display = ("user", "date", "seconds", "hours")
    list_filter = ("date",)
    search_fields = ("user__username", "user__display_name")
    autocomplete_fields = ("user",)
    date_hierarchy = "date"
    readonly_fields = ()


@admin.register(TelegramOTP)
class TelegramOTPAdmin(BaseModelAdmin):
    list_display = ("code", "telegram_id", "telegram_username", "status", "created_at", "expires_at")
    list_filter = ("is_used", "created_at")
    search_fields = ("code", "telegram_id", "telegram_username")

    @admin.display(description="Holat")
    def status(self, obj):
        if obj.is_used:
            return format_html('<span style="color:#94a3b8">ishlatilgan</span>')
        if obj.is_valid:
            return format_html('<span style="color:#059669;font-weight:700">faol</span>')
        return format_html('<span style="color:#ef4444">muddati tugagan</span>')


@admin.register(TestAccountLogin)
class TestAccountLoginAdmin(BaseModelAdmin):
    """Doimiy test akkauntga har kirish — qachon, qayerdan."""

    list_display = ("created_at", "user", "ip_address", "user_agent")
    list_filter = ("created_at",)
    search_fields = ("user__username", "ip_address", "user_agent")
    date_hierarchy = "created_at"
    autocomplete_fields = ("user",)


@admin.register(ActiveSession)
class ActiveSessionAdmin(BaseModelAdmin):
    """Har (foydalanuvchi, platforma) uchun faol sessiya — oxirgi kirish vaqti."""

    list_display = ("user", "platform", "label", "ip_address", "last_seen_at", "created_at")
    list_filter = ("platform", "last_seen_at")
    search_fields = ("user__username", "user__display_name", "ip_address", "device")
    autocomplete_fields = ("user",)
    readonly_fields = ("sid", "label", "created_at", "updated_at")

    @admin.display(description="Qurilma")
    def label(self, obj):
        return obj.label


@admin.register(Invitation)
class InvitationAdmin(BaseModelAdmin):
    """Hisobga olingan takliflar. Faqat o'qish — sovg'a hisobi shunga tayanadi."""

    list_display = ("inviter", "invitee", "source", "notified", "created_at")
    list_filter = ("source", "notified", "created_at")
    search_fields = ("inviter__username", "inviter__display_name",
                     "invitee__username", "invitee__display_name")
    autocomplete_fields = ("inviter", "invitee")
    list_select_related = ("inviter", "invitee")
    readonly_fields = ("inviter", "invitee", "source", "notified", "created_at", "updated_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(PendingInvite)
class PendingInviteAdmin(BaseModelAdmin):
    """Botga havola bilan kirgan, lekin hali ro'yxatdan o'tmaganlar."""

    list_display = ("telegram_id", "inviter", "created_at")
    search_fields = ("telegram_id", "inviter__username", "inviter__display_name")
    autocomplete_fields = ("inviter",)
    list_select_related = ("inviter",)

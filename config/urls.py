"""URL xaritasi."""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from apps.accounts import session_views as account_session_views
from apps.accounts import views as account_views
from apps.accounts.refresh import SessionTokenRefreshView
from apps.billing import views as billing_views
from apps.catalog import views as catalog_views
from apps.catalog import legacy_views

admin.site.site_header = "listening.uz boshqaruvi"
admin.site.site_title = "listening.uz"
admin.site.index_title = "Diktantlar va foydalanuvchilar"

router = DefaultRouter()
router.register("dictations", catalog_views.DictationViewSet, basename="dictation")
router.register("shorts", catalog_views.ShortViewSet, basename="short")
router.register("ielts-tests", catalog_views.IeltsListeningTestViewSet, basename="ielts-test")

api_patterns = [
    # Auth
    path("auth/telegram/verify/", account_views.TelegramVerifyView.as_view(), name="telegram-verify"),
    path("auth/refresh/", SessionTokenRefreshView.as_view(), name="token-refresh"),
    path("auth/setup/", account_views.ProfileSetupView.as_view(), name="profile-setup"),
    path("auth/logout/", account_session_views.logout, name="logout"),

    # Sessiyalar (qurilmalar) — sayt ham, ilova ham shu yerdan boshqaradi
    path("me/sessions/", account_session_views.my_sessions, name="me-sessions"),
    path("me/sessions/revoke/", account_session_views.revoke_session, name="me-sessions-revoke"),

    # Profil
    path("me/", account_views.MeView.as_view(), name="me"),
    path("me/stats/", account_views.my_stats, name="me-stats"),
    path("me/limits/", account_views.my_limits, name="me-limits"),
    path("me/invites/", account_views.my_invites, name="me-invites"),
    path("me/subscriptions/", account_views.my_subscriptions, name="me-subscriptions"),
    path("auth/username-check/", account_views.username_check, name="username-check"),
    path("me/activity/", account_views.my_activity, name="me-activity"),
    path("me/activity/track/", account_views.track_activity, name="me-track"),
    path("me/vocab/", legacy_views.categories_list, name="me-vocab"),
    path("me/attempts/", legacy_views.categories_list, name="me-attempts"),
    path("leaderboard/", account_views.leaderboard, name="leaderboard"),

    # Diktantlar (yangi asosiy API)
    path("dictations/types/", catalog_views.dictation_types, name="dictation-types"),

    # Eski API — bo'sh javob qaytaradi (frontend crash bo'lmasin)
    path("home/", legacy_views.home, name="home"),
    path("config/", legacy_views.site_config, name="site-config"),
    path("app-ad/", legacy_views.app_ad, name="app-ad"),
    path("categories/", legacy_views.categories_list, name="categories"),
    path("categories/<slug:slug>/groups/", legacy_views.category_groups, name="category-groups"),
    path("content/", legacy_views.content_list, name="content-list"),
    path("content/<int:pk>/", legacy_views.content_detail, name="content-detail"),
    path("feed/shorts/", legacy_views.shorts_feed, name="shorts-feed"),

    # Tariflar
    path("billing/plans/", billing_views.plans, name="plans"),
    path("billing/subscribe/", billing_views.subscribe, name="subscribe"),

    path("", include(router.urls)),
]

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include(api_patterns)),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

from rest_framework import permissions


class IsOwner(permissions.BasePermission):
    """Obyekt faqat egasiga tegishli bo'lsa ruxsat beradi."""

    def has_object_permission(self, request, view, obj):
        return getattr(obj, "user_id", None) == request.user.id


class ReadOnlyOrStaff(permissions.BasePermission):
    """O'qish hammaga, yozish faqat xodimlarga."""

    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True
        return bool(request.user and request.user.is_staff)

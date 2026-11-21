from rest_framework.permissions import BasePermission

from .models import PlatformSettings
from .platform import FeatureUnavailable


class IsModeratorOrAbove(BasePermission):
    """Moderators can handle moderation queues; admins and superusers can too."""
    message = "Moderator access is required."

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user and user.is_authenticated and user.is_active and not user.is_suspended
            and user.is_staff
            and (user.is_superuser or user.role in ("moderator", "admin"))
        )


class IsAdminOrAbove(BasePermission):
    """Only administrators and superusers can manage accounts and operations."""
    message = "Administrator access is required."

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user and user.is_authenticated and user.is_active and not user.is_suspended
            and user.is_staff and (user.is_superuser or user.role == "admin")
        )


class PlatformFeatureEnabled(BasePermission):
    def has_permission(self, request, view):
        feature = getattr(view, "platform_feature", None)
        if not feature or getattr(PlatformSettings.get_solo(), feature, False):
            return True
        raise FeatureUnavailable("This feature is currently disabled by the school administrator.")



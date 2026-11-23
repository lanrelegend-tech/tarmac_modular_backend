from rest_framework.permissions import BasePermission

from .models import AnnouncementPublisher, PlatformSettings
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


def has_announcement_publisher_access(user):
    if not user or not user.is_authenticated or not user.is_active or user.is_suspended:
        return False
    if user.is_staff:
        return False
    return AnnouncementPublisher.objects.filter(user_id=user.pk, is_active=True).exists()


class IsAnnouncementPublisherOrModerator(BasePermission):
    """Allow only announcement publishers and existing moderation staff."""
    message = "Announcement publisher access is required."

    def has_permission(self, request, view):
        return IsModeratorOrAbove().has_permission(request, view) or has_announcement_publisher_access(request.user)


class PlatformFeatureEnabled(BasePermission):
    def has_permission(self, request, view):
        feature = getattr(view, "platform_feature", None)
        if not feature or getattr(PlatformSettings.get_solo(), feature, False):
            return True
        raise FeatureUnavailable("This feature is currently disabled by the school administrator.")



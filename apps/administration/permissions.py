from rest_framework.permissions import BasePermission

from .models import PlatformSettings
from .platform import FeatureUnavailable


class PlatformFeatureEnabled(BasePermission):
    def has_permission(self, request, view):
        feature = getattr(view, "platform_feature", None)
        if not feature or getattr(PlatformSettings.get_solo(), feature, False):
            return True
        raise FeatureUnavailable("This feature is currently disabled by the school administrator.")



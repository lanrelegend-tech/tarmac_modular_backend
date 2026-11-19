from rest_framework.exceptions import APIException

from .models import PlatformSettings


class FeatureUnavailable(APIException):
    status_code = 503
    default_detail = "This feature is currently unavailable."
    default_code = "feature_unavailable"


def require_feature(feature):
    if not getattr(PlatformSettings.get_solo(), feature, False):
        raise FeatureUnavailable()

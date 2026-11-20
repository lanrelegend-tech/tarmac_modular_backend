from rest_framework.permissions import BasePermission

def is_student_feature_eligible(user):
    if not (user and user.is_authenticated and user.is_active and not user.is_suspended):
        return False
    from apps.administration.models import PlatformSettings
    platform = PlatformSettings.get_solo()
    email_ok = user.email_verified or not platform.email_verification_required
    student_ok = user.is_student_verified or not platform.student_verification_enabled
    return bool(email_ok and student_ok)

class IsActiveAccount(BasePermission):
    message = "Your account is suspended or inactive. Contact the school administrator."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_active and not user.is_suspended)

class IsVerifiedStudent(BasePermission):
    message = "An active, verified student account is required."

    def has_permission(self, request, view):
        return is_student_feature_eligible(request.user)

from rest_framework.permissions import BasePermission

class IsActiveAccount(BasePermission):
    message = "Your account is suspended or inactive. Contact the school administrator."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_active and not user.is_suspended)

class IsVerifiedStudent(BasePermission):
    message = "An active, verified student account is required."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_active and user.email_verified and user.is_student_verified and not user.is_suspended)

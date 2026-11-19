from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import RegisterView, StudentPublicProfileView, VerificationViewSet, me, student_id_image, profile_image, confirm_email, request_password_reset, confirm_password_reset, resend_verification_email
router=DefaultRouter(); router.register("verification", VerificationViewSet, basename="verification")
urlpatterns=[path("register/", RegisterView.as_view()), path("me/", me), path("students/<int:pk>/", StudentPublicProfileView.as_view()), path("profile-image/<int:user_id>/", profile_image), path("email/confirm/", confirm_email), path("email/resend/", resend_verification_email), path("password-reset/", request_password_reset), path("password-reset/confirm/", confirm_password_reset), path("staff/verification/<int:verification_id>/image/", student_id_image, name="student-id-image"), path("", include(router.urls))]

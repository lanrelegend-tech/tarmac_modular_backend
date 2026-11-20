import mimetypes
import logging
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework import generics, permissions, viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework import status
from rest_framework.decorators import throttle_classes
from rest_framework.throttling import AnonRateThrottle
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from .models import User, StudentVerification, AccountActionToken
from .serializers import RegisterSerializer, UserSerializer, StudentPublicProfileSerializer, StudentVerificationSerializer, ProfileUpdateSerializer, EmailTokenSerializer, PasswordResetRequestSerializer, PasswordResetConfirmSerializer
from .permissions import IsVerifiedStudent
from .services import consume_account_action_token, issue_account_action_token

logger = logging.getLogger(__name__)
class AccountActionThrottle(AnonRateThrottle):
    scope = "account_action"
class RegisterView(generics.CreateAPIView):
    queryset=User.objects.all(); serializer_class=RegisterSerializer; permission_classes=[permissions.AllowAny]
    def create(self, request, *args, **kwargs):
        from apps.administration.models import PlatformSettings
        from rest_framework.exceptions import PermissionDenied
        if not PlatformSettings.get_solo().registration_enabled: raise PermissionDenied("Student registration is currently closed.")
        return super().create(request, *args, **kwargs)
class StudentPublicProfileView(generics.RetrieveAPIView):
    queryset = User.objects.filter(is_active=True, is_student_verified=True, is_suspended=False)
    serializer_class = StudentPublicProfileSerializer
    permission_classes = [IsVerifiedStudent]
@api_view(["GET", "PATCH"])
@permission_classes([permissions.IsAuthenticated])
def me(request):
    if request.method == "PATCH":
        serializer = ProfileUpdateSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
    return Response(UserSerializer(request.user).data)

@staff_member_required
def student_id_image(request, verification_id):
    verification = get_object_or_404(StudentVerification, pk=verification_id)
    content_type = mimetypes.guess_type(verification.student_id_image.name)[0] or "application/octet-stream"
    return FileResponse(verification.student_id_image.open("rb"), content_type=content_type)

@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def profile_image(request, user_id):
    target = get_object_or_404(User, pk=user_id, is_active=True)
    if request.user.pk != user_id and not (request.user.is_student_verified and not request.user.is_suspended and target.is_student_verified and not target.is_suspended):
        from rest_framework.exceptions import PermissionDenied
        raise PermissionDenied("An active verified student account is required to view another student's profile image.")
    if not target.profile_picture:
        from rest_framework.exceptions import NotFound
        raise NotFound("No profile picture has been uploaded.")
    content_type = mimetypes.guess_type(target.profile_picture.name)[0] or "application/octet-stream"
    return FileResponse(target.profile_picture.open("rb"), content_type=content_type)

@api_view(["POST"])
@throttle_classes([AccountActionThrottle])
@permission_classes([permissions.AllowAny])
def confirm_email(request):
    serializer = EmailTokenSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    user = consume_account_action_token(serializer.validated_data["email"], serializer.validated_data["token"], AccountActionToken.Purpose.EMAIL_VERIFY)
    if not user:
        raise ValidationError({"token": "This verification link is invalid or expired."})
    user.email_verified = True
    user.save(update_fields=("email_verified",))
    return Response({"email_verified": True})

@api_view(["POST"])
@throttle_classes([AccountActionThrottle])
@permission_classes([permissions.AllowAny])
def request_password_reset(request):
    serializer = PasswordResetRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    user = User.objects.filter(email__iexact=serializer.validated_data["email"], is_active=True).first()
    if user:
        issue_account_action_token(user, AccountActionToken.Purpose.PASSWORD_RESET)
    return Response({"detail": "If that account exists, password reset instructions have been sent."})

@api_view(["POST"])
@throttle_classes([AccountActionThrottle])
@permission_classes([permissions.AllowAny])
def confirm_password_reset(request):
    serializer = PasswordResetConfirmSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    user = consume_account_action_token(serializer.validated_data["email"], serializer.validated_data["token"], AccountActionToken.Purpose.PASSWORD_RESET)
    if not user:
        raise ValidationError({"token": "This reset link is invalid or expired."})
    with transaction.atomic():
        user.set_password(serializer.validated_data["new_password"])
        user.save(update_fields=("password",))
        for outstanding in OutstandingToken.objects.filter(user=user):
            BlacklistedToken.objects.get_or_create(token=outstanding)
    return Response({"detail": "Your password has been reset. Sign in with your new password."})

@api_view(["POST"])
@throttle_classes([AccountActionThrottle])
@permission_classes([permissions.IsAuthenticated])
def resend_verification_email(request):
    if request.user.email_verified:
        return Response({"email_verified": True})
    issue_account_action_token(request.user, AccountActionToken.Purpose.EMAIL_VERIFY)
    return Response({"detail": "Verification instructions have been sent if email delivery is available."})
class VerificationViewSet(viewsets.ModelViewSet):
    serializer_class=StudentVerificationSerializer; permission_classes=[permissions.IsAuthenticated]
    def get_queryset(self): return StudentVerification.objects.filter(user=self.request.user)
    def perform_create(self, serializer):
        from apps.administration.models import PlatformSettings
        from django.utils import timezone
        from rest_framework.exceptions import ValidationError
        platform = PlatformSettings.get_solo()
        matric = serializer.validated_data["submitted_matric_number"]
        if platform.auto_approve_students:
            if not self.request.user.email_verified: raise ValidationError("Verify your email before automatic student approval.")
            if User.objects.filter(matric_number__iexact=matric).exclude(pk=self.request.user.pk).exists(): raise ValidationError({"submitted_matric_number": "This matric number is already assigned."})
            serializer.save(user=self.request.user, status=StudentVerification.Status.APPROVED, reviewed_at=timezone.now())
            self.request.user.matric_number = matric; self.request.user.is_student_verified = True
            self.request.user.save(update_fields=("matric_number", "is_student_verified"))
        else:
            serializer.save(user=self.request.user)
    def perform_destroy(self, instance):
        if instance.status != StudentVerification.Status.PENDING:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Reviewed verification submissions cannot be deleted.")
        instance.delete()

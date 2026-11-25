import logging
import hashlib
from datetime import timedelta
from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from config.storage import optimized_image_response
from django.shortcuts import get_object_or_404
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import generics, permissions, viewsets
from rest_framework.pagination import PageNumberPagination
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework import status
from rest_framework.decorators import throttle_classes, throttle_scope
from rest_framework.throttling import AnonRateThrottle, ScopedRateThrottle
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from .models import User, StudentVerification, AccountActionToken, ProfileChangeRequest, ProfileChangeMessage, CourseProgram, AnonymousMarketplaceIdentity
from .serializers import RegisterSerializer, UserSerializer, StudentPublicProfileSerializer, StudentVerificationSerializer, ProfileUpdateSerializer, StudentProfileChangeRequestSerializer, EmailTokenSerializer, PasswordResetRequestSerializer, PasswordResetConfirmSerializer, CourseProgramSerializer, AnonymousMarketplaceIdentitySerializer
from .permissions import IsVerifiedStudent, is_student_feature_eligible
from apps.administration.permissions import IsAdminOrAbove
from .services import consume_account_action_token, issue_account_action_token
from .utils import username_change_available_at
from config.throttles import ActionRateLimitMixin

logger = logging.getLogger(__name__)
class AccountActionThrottle(AnonRateThrottle):
    scope = "account_action"

class UsernameAvailabilityThrottle(AnonRateThrottle):
    scope = "username_availability"

class RegistrationThrottle(AnonRateThrottle):
    scope = "registration"

    def get_cache_key(self, request, view):
        identity = self.get_ident(request)
        email = str(request.data.get("email", "")).strip().casefold()
        digest = hashlib.sha256(f"{identity}:{email}".encode("utf-8")).hexdigest()
        return self.cache_format % {"scope": self.scope, "ident": digest}

class SuspensionAppealView(generics.GenericAPIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [AccountActionThrottle]

    def post(self, request):
        from apps.administration.models import SupportTicket, SupportMessage, AdminLog
        raw_token = str(request.data.get("token", "")).strip()
        reason = str(request.data.get("reason", "")).strip()
        if len(reason) < 20 or len(reason) > 3000:
            raise ValidationError({"reason": "Explain your appeal in 20 to 3000 characters."})
        digest = hashlib.sha256(raw_token.encode()).hexdigest()
        with transaction.atomic():
            action_token = AccountActionToken.objects.select_for_update().select_related("user").filter(
                token_hash=digest,
                purpose=AccountActionToken.Purpose.SUSPENSION_APPEAL,
                used_at__isnull=True,
                expires_at__gt=timezone.now(),
                user__is_active=True,
                user__is_suspended=True,
            ).first()
            if not action_token:
                raise ValidationError({"detail": "This appeal link is invalid, expired, or already used."})
            ticket = SupportTicket.objects.create(user=action_token.user, subject="Account suspension appeal", category="account_appeal", priority=SupportTicket.Priority.HIGH)
            SupportMessage.objects.create(ticket=ticket, sender=action_token.user, body=reason)
            action_token.used_at = timezone.now()
            action_token.save(update_fields=("used_at",))
        AdminLog.objects.create(actor=None, action="student_suspension_appeal_submitted", target_type="SupportTicket", target_id=str(ticket.pk), metadata={"user_id": action_token.user_id})
        from apps.notifications.staff import notify_staff_section
        notify_staff_section("support", "New account suspension appeal", action_token.user.username, {"kind": "support_ticket", "ticket_id": ticket.pk}, f"suspension-appeal:{ticket.pk}")
        return Response({"detail": "Your appeal was sent to Tarmac Support. You will receive an email when it has been reviewed."}, status=status.HTTP_201_CREATED)


@api_view(["GET"])
@permission_classes([permissions.AllowAny])
@throttle_classes([UsernameAvailabilityThrottle])
def username_availability(request):
    username = str(request.query_params.get("username", "")).strip()
    if not username:
        return Response({"available": False, "message": "Enter a username to check."})
    try:
        User._meta.get_field("username").run_validators(username)
    except DjangoValidationError as exc:
        return Response({"available": False, "message": " ".join(exc.messages)})

    matches = User.objects.filter(username__iexact=username)
    current_user = request.user if request.user.is_authenticated else None
    same_as_current = bool(current_user and current_user.username.casefold() == username.casefold())
    if current_user:
        matches = matches.exclude(pk=current_user.pk)
    available = same_as_current or not matches.exists()
    payload = {"available": available, "message": "Username is available." if available else "That username is already taken."}
    if current_user:
        can_change = same_as_current or not current_user.username_changed_at or timezone.now() >= username_change_available_at(current_user.username_changed_at)
        payload["can_change"] = can_change
        if not can_change:
            payload["message"] = "You can change your username once every 3 months. Please wait until your next change date."
    return Response(payload)


@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
@throttle_classes([ScopedRateThrottle])
@throttle_scope("account_action")
def request_account_deletion(request):
    user = request.user
    if user.is_staff or user.role != User.Role.STUDENT:
        raise ValidationError({"detail": "Administrator accounts cannot be deleted through student profile settings."})
    if not user.email_verified:
        raise ValidationError({"detail": "Verify your email address before requesting account deletion."})
    if user.deleted_at:
        raise ValidationError({"detail": "This account has already been deleted."})
    issue_account_action_token(user, AccountActionToken.Purpose.ACCOUNT_DELETE)
    return Response({"detail": f"We sent a confirmation link to {user.email}. Your account stays active until you open that link."})


@api_view(["POST"])
@permission_classes([permissions.AllowAny])
@throttle_classes([AccountActionThrottle])
@transaction.atomic
def confirm_account_deletion(request):
    serializer = EmailTokenSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    user = consume_account_action_token(
        serializer.validated_data["email"],
        serializer.validated_data["token"],
        AccountActionToken.Purpose.ACCOUNT_DELETE,
    )
    if not user:
        raise ValidationError({"token": "This account deletion link is invalid or expired. Sign in and request a new one."})

    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=user.pk)
        if user.is_staff or user.role != User.Role.STUDENT:
            raise ValidationError({"detail": "Administrator accounts cannot be deleted through student profile settings."})
        if user.deleted_at:
            raise ValidationError({"detail": "This account has already been deleted."})
        now = timezone.now()
        user.deleted_at = now
        user.is_active = False
        user.is_suspended = False
        user.public_profile_enabled = False
        user.save(update_fields=("deleted_at", "is_active", "is_suspended", "public_profile_enabled"))
        AnonymousMarketplaceIdentity.objects.filter(user=user).update(is_enabled=False)

        from apps.products.models import Product
        from apps.jobs.models import JobListing
        from apps.lost_found.models import LostFoundReport
        from apps.ads.models import AdCampaign
        Product.objects.filter(seller=user).exclude(status__in=(Product.Status.SOLD, Product.Status.REMOVED)).update(status=Product.Status.REMOVED, is_featured=False)
        JobListing.objects.filter(owner=user).exclude(status__in=(JobListing.Status.FILLED, JobListing.Status.EXPIRED, JobListing.Status.REJECTED, JobListing.Status.REMOVED)).update(status=JobListing.Status.REMOVED)
        LostFoundReport.objects.filter(reporter=user, is_active=True).update(is_active=False)
        AdCampaign.objects.filter(advertiser=user, status__in=(AdCampaign.Status.ACTIVE, AdCampaign.Status.SCHEDULED)).update(status=AdCampaign.Status.PAUSED)
        AdCampaign.objects.filter(advertiser=user, status__in=(AdCampaign.Status.PENDING_PAYMENT, AdCampaign.Status.PENDING_REVIEW)).update(status=AdCampaign.Status.CANCELLED)

        for verification in StudentVerification.objects.filter(user=user).exclude(student_id_image=""):
            from .services import delete_verification_id_image
            delete_verification_id_image(verification)
            verification.save(update_fields=("student_id_image",))
        for outstanding in OutstandingToken.objects.filter(user=user):
            BlacklistedToken.objects.get_or_create(token=outstanding)

        try:
            from apps.administration.models import AdminLog
            AdminLog.objects.create(actor=None, action="student_account_deleted", target_type="User", target_id=str(user.pk), metadata={"public_id": str(user.public_id)})
        except Exception:
            logger.exception("Could not record account deletion audit entry for user %s", user.pk)

    response = Response({"detail": "Your Tarmac account has been deleted. The school can restore access if you contact support."})
    response.delete_cookie(settings.AUTH_ACCESS_COOKIE, path="/", samesite=settings.AUTH_COOKIE_SAMESITE)
    response.delete_cookie(settings.AUTH_REFRESH_COOKIE, path="/api/auth/", samesite=settings.AUTH_COOKIE_SAMESITE)
    return response

class CourseProgramPagination(PageNumberPagination):
    page_size = 100
    page_size_query_param = "page_size"
    max_page_size = 200


class CourseProgramViewSet(viewsets.ModelViewSet):
    serializer_class = CourseProgramSerializer
    queryset = CourseProgram.objects.all()
    pagination_class = CourseProgramPagination
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        queryset = CourseProgram.objects.all()
        return queryset if self.request.user.is_staff else queryset.filter(is_active=True)

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [permissions.AllowAny()]
        return [IsAdminOrAbove()]

    def perform_update(self, serializer):
        program = serializer.save()
        User.objects.filter(course=program).update(department=program.name, faculty=program.faculty)

    def perform_destroy(self, instance):
        if User.objects.filter(course=instance).exists():
            raise ValidationError({"detail": "This department is assigned to student accounts. Deactivate it to remove it from signup while preserving their records."})
        instance.delete()

class RegisterView(generics.CreateAPIView):
    queryset=User.objects.all(); serializer_class=RegisterSerializer; permission_classes=[permissions.AllowAny]
    throttle_classes = [AnonRateThrottle, RegistrationThrottle]
    def create(self, request, *args, **kwargs):
        from apps.administration.models import PlatformSettings
        from rest_framework.exceptions import PermissionDenied
        if not PlatformSettings.get_solo().registration_enabled: raise PermissionDenied("Student registration is currently closed.")
        return super().create(request, *args, **kwargs)
class StudentPublicProfileView(generics.RetrieveAPIView):
    serializer_class = StudentPublicProfileSerializer
    permission_classes = [IsVerifiedStudent]
    def get_queryset(self):
        queryset = User.objects.filter(is_active=True, is_suspended=False, public_profile_enabled=True)
        from apps.administration.models import PlatformSettings
        platform = PlatformSettings.get_solo()
        if platform.student_verification_enabled:
            queryset = queryset.filter(is_student_verified=True)
        if platform.email_verification_required:
            queryset = queryset.filter(email_verified=True)
        return queryset

    def get_object(self):
        return get_object_or_404(self.get_queryset(), public_id=self.kwargs["public_id"])

class AnonymousIdentityView(generics.RetrieveUpdateAPIView):
    serializer_class = AnonymousMarketplaceIdentitySerializer
    permission_classes = [IsVerifiedStudent]
    http_method_names = ["get", "post", "patch", "head", "options"]
    def get_object(self):
        return getattr(self.request.user, "anonymous_marketplace_identity", None)
    def retrieve(self, request, *args, **kwargs):
        identity = self.get_object()
        return Response(self.get_serializer(identity).data if identity else None)
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=201)

@api_view(["GET"])
@permission_classes([IsVerifiedStudent])
def public_marketplace_identity(request, public_id):
    from apps.products.models import Product
    from apps.jobs.models import JobListing
    identity = AnonymousMarketplaceIdentity.objects.select_related("user").filter(public_id=public_id, user__is_active=True, user__is_suspended=False, user__is_student_verified=True).first()
    is_owner = bool(identity and identity.user_id == request.user.pk)
    if identity and not identity.is_enabled and not is_owner:
        identity = None
    if not identity:
        from rest_framework.exceptions import NotFound
        raise NotFound("This anonymous marketplace identity is unavailable.")
    user = identity.user
    products = Product.objects.filter(seller=user, anonymous_identity=identity, identity_type=Product.IdentityType.ANONYMOUS, show_on_profile=True, visibility=Product.Visibility.PUBLIC, status=Product.Status.ACTIVE).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())).select_related("category").prefetch_related("images")
    from apps.products.serializers import ProductSerializer
    jobs = JobListing.objects.filter(owner=user, anonymous_identity=identity, identity_type="anonymous", source=JobListing.Source.STUDENT, status=JobListing.Status.ACTIVE, deadline__gte=timezone.localdate(), category__is_active=True).select_related("category", "anonymous_identity")
    from apps.jobs.serializers import JobListingSerializer
    return Response({"public_id": str(identity.public_id), "handle": identity.handle, "campus": user.campus, "area": user.area, "is_enabled": identity.is_enabled, "is_owner": is_owner, "is_student_verified": user.is_student_verified, "student_status": user.student_status, "last_seen_at": user.last_seen_at, "products": ProductSerializer(products, many=True, context={"request": request}).data, "jobs": JobListingSerializer(jobs, many=True, context={"request": request}).data})

@api_view(["GET"])
@permission_classes([IsVerifiedStudent])
def student_directory(request):
    query = str(request.query_params.get("search", "")).strip()
    if len(query) < 2:
        return Response([])
    from django.db.models import Q
    from .permissions import is_student_feature_eligible
    from .models import AnonymousMarketplaceIdentity
    from apps.administration.models import PlatformSettings

    platform = PlatformSettings.get_solo()
    words = query.split(maxsplit=1)
    profile_search = Q(username__istartswith=query) | (Q(public_name_enabled=True) & (Q(first_name__istartswith=query) | Q(last_name__istartswith=query)))
    if len(words) == 2:
        profile_search |= Q(public_name_enabled=True, first_name__istartswith=words[0], last_name__istartswith=words[1])

    users = User.objects.filter(
        profile_search,
        is_active=True,
        is_suspended=False,
        deleted_at__isnull=True,
        public_profile_enabled=True,
    )
    if platform.student_verification_enabled:
        users = users.filter(is_student_verified=True)
    if platform.email_verification_required:
        users = users.filter(email_verified=True)
    users = list(users.order_by("username")[:30])

    results = []
    for user in users:
        if not is_student_feature_eligible(user):
            continue
        results.append({
            "public_id": str(user.public_id),
            "identity_type": "normal",
            "username": user.username,
            "display_name": (user.get_full_name().strip() if user.public_name_enabled else "") or user.username,
            "student_status": user.student_status,
            "campus": user.campus,
            "department": user.department if user.public_department_enabled else "",
            "level": user.level if user.public_level_enabled else "",
            "is_hostel_agent": user.is_hostel_agent,
        })

    identities = AnonymousMarketplaceIdentity.objects.select_related("user").filter(
        handle__istartswith=query,
        is_enabled=True,
        user__is_active=True,
        user__is_suspended=False,
        user__deleted_at__isnull=True,
    )
    if platform.student_verification_enabled:
        identities = identities.filter(user__is_student_verified=True)
    if platform.email_verification_required:
        identities = identities.filter(user__email_verified=True)
    for identity in identities.order_by("handle")[:30]:
        if not is_student_feature_eligible(identity.user):
            continue
        results.append({
            "public_id": str(identity.public_id),
            "identity_type": "anonymous",
            "username": identity.handle,
            "display_name": identity.handle,
            "student_status": identity.user.student_status,
            "campus": identity.user.campus,
            "department": "",
            "level": "",
            "is_hostel_agent": identity.user.is_hostel_agent,
        })

    results.sort(key=lambda profile: (profile["display_name"].casefold(), profile["identity_type"]))
    return Response(results[:30])
@api_view(["GET", "PATCH"])
@permission_classes([permissions.IsAuthenticated])
def me(request):
    if request.method == "PATCH":
        allowed_preferences = {"notification_email_enabled", "notification_whatsapp_enabled", "public_name_enabled", "public_department_enabled", "public_level_enabled", "public_profile_enabled", "public_profile_picture_enabled"}
        if not set(request.data.keys()).issubset(allowed_preferences):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Profile details cannot be edited directly. Submit a profile change request for administrator review.")
        serializer = ProfileUpdateSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
    return Response(UserSerializer(request.user, context={"request": request}).data)


@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
@throttle_classes([ScopedRateThrottle])
@throttle_scope("presence")
def presence_heartbeat(request):
    now = timezone.now()
    changed = User.objects.filter(pk=request.user.pk).filter(
        Q(last_seen_at__isnull=True) | Q(last_seen_at__lt=now - timedelta(minutes=1))
    ).update(last_seen_at=now)
    if changed:
        request.user.last_seen_at = now
    return Response({"last_seen_at": request.user.last_seen_at})


class ProfileChangeRequestViewSet(ActionRateLimitMixin, viewsets.ModelViewSet):
    action_throttle_scopes = {
        "create": "profile_change_request",
        "reply": "profile_change_reply",
    }
    serializer_class = StudentProfileChangeRequestSerializer
    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        return ProfileChangeRequest.objects.filter(user=self.request.user).prefetch_related("messages__sender")

    def perform_create(self, serializer):
        change_request = serializer.save()
        from apps.notifications.staff import notify_staff_section
        notify_staff_section("profile_changes", "Profile change request", f"{change_request.user.get_full_name() or change_request.user.username} requested a profile change.", {"kind": "profile_change_request", "request_id": change_request.pk}, f"profile-change:{change_request.pk}")

    @action(detail=True, methods=["post"])
    def reply(self, request, pk=None):
        change_request = self.get_object()
        if change_request.status != ProfileChangeRequest.Status.NEEDS_INFO:
            raise ValidationError("There is no administrator question waiting for a reply.")
        body = str(request.data.get("body", "")).strip()
        if len(body) < 2 or len(body) > 3000:
            raise ValidationError({"body": "Write a reply between 2 and 3000 characters."})
        message = ProfileChangeMessage.objects.create(request=change_request, sender=request.user, body=body)
        change_request.status = ProfileChangeRequest.Status.PENDING
        change_request.save(update_fields=("status", "updated_at"))
        from apps.notifications.staff import notify_staff_section
        notify_staff_section(
            "profile_changes",
            "Student replied to a profile change question",
            f"{request.user.get_full_name().strip() or request.user.username} replied to profile change request #{change_request.pk}. Open the request in Tarmac to review the answer.",
            {"kind": "profile_change_reply", "request_id": change_request.pk},
            f"profile-change-reply:{message.pk}",
        )
        return Response(self.get_serializer(change_request).data)


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def profile_change_request_image(request, request_id):
    change_request = get_object_or_404(ProfileChangeRequest, pk=request_id, user=request.user)
    if not change_request.requested_profile_picture:
        from rest_framework.exceptions import NotFound
        raise NotFound("No profile picture was included in this request.")
    return optimized_image_response(change_request.requested_profile_picture)

@staff_member_required
def student_id_image(request, verification_id):
    verification = get_object_or_404(StudentVerification, pk=verification_id)
    if not verification.student_id_image:
        from django.http import Http404
        raise Http404("This verification image has been deleted.")
    return optimized_image_response(verification.student_id_image)

@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def profile_image(request, user_id):
    target = get_object_or_404(User, public_id=user_id, is_active=True)
    is_owner = request.user.pk == target.pk
    is_admin = request.user.is_staff or request.user.is_superuser
    if not is_owner and not is_admin and not (target.public_profile_enabled and target.public_profile_picture_enabled and is_student_feature_eligible(request.user) and is_student_feature_eligible(target)):
        from rest_framework.exceptions import PermissionDenied
        raise PermissionDenied("An active verified student account is required to view another student's profile image.")
    if not target.profile_picture:
        from rest_framework.exceptions import NotFound
        raise NotFound("No profile picture has been uploaded.")
    # This URL is access-controlled above. Keep a short browser cache to avoid
    # downloading the same avatar from Cloudinary on every page visit.
    return optimized_image_response(target.profile_picture, cache_seconds=300)

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
@permission_classes([permissions.AllowAny])
def resend_verification_email(request):
    if request.user.is_authenticated:
        user = request.user
    else:
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = User.objects.filter(email__iexact=serializer.validated_data["email"], is_active=True).first()
    if user and not user.email_verified and not user.is_suspended:
        issue_account_action_token(user, AccountActionToken.Purpose.EMAIL_VERIFY)
    return Response({"detail": "If that email belongs to an unverified Tarmac account, another verification link will be sent."})
class VerificationViewSet(ActionRateLimitMixin, viewsets.ModelViewSet):
    action_throttle_scopes = {
        "create": "student_verification_submit",
        "update": "student_verification_submit",
        "partial_update": "student_verification_submit",
        "destroy": "student_verification_submit",
    }
    serializer_class=StudentVerificationSerializer; permission_classes=[permissions.IsAuthenticated]
    def get_queryset(self): return StudentVerification.objects.filter(user=self.request.user).order_by("-created_at", "-pk")
    @transaction.atomic
    def perform_update(self, serializer):
        if serializer.validated_data.get("student_id_image") and serializer.instance.student_id_image:
            from .services import delete_verification_id_image
            delete_verification_id_image(serializer.instance)
        profile_photo = serializer.validated_data.pop("profile_picture", None)
        serializer.validated_data.pop("profile_photo_consent", None)
        verification = serializer.save()
        if profile_photo:
            from .services import save_account_profile_photo
            save_account_profile_photo(self.request.user, profile_photo)
        if verification.status == StudentVerification.Status.PENDING:
            from apps.notifications.staff import notify_staff_section
            notify_staff_section("verification", "Student verification needs review", f"{self.request.user.get_full_name().strip() or self.request.user.username} submitted student verification.", {"kind": "student_verification_review", "verification_id": verification.pk}, f"student-verification:{verification.pk}:{verification.updated_at.timestamp()}")
    def perform_create(self, serializer):
        from apps.administration.models import PlatformSettings
        from django.utils import timezone
        from rest_framework.exceptions import ValidationError
        platform = PlatformSettings.get_solo()
        matric = serializer.validated_data["submitted_matric_number"]
        profile_photo = serializer.validated_data.pop("profile_picture", None)
        serializer.validated_data.pop("profile_photo_consent", None)
        if platform.auto_approve_students and serializer.validated_data.get("requested_affiliation") == "student":
            if platform.email_verification_required and not self.request.user.email_verified: raise ValidationError("Verify your email before automatic student approval.")
            if User.objects.filter(matric_number__iexact=matric).exclude(pk=self.request.user.pk).exists(): raise ValidationError({"submitted_matric_number": "This matric number is already assigned."})
            # No administrator needs to inspect the ID when auto-approval is enabled;
            # never persist that uploaded document in this path.
            serializer.validated_data.pop("student_id_image", None)
            verification = serializer.save(user=self.request.user, status=StudentVerification.Status.APPROVED, reviewed_at=timezone.now())
            self.request.user.matric_number = matric; self.request.user.is_student_verified = True
            self.request.user.verified_affiliation = verification.requested_affiliation
            self.request.user.student_status_override = None
            user_fields = ["matric_number", "is_student_verified", "verified_affiliation", "student_status_override"]
            if profile_photo:
                from .services import save_account_profile_photo
                save_account_profile_photo(self.request.user, profile_photo)
            self.request.user.save(update_fields=user_fields)
        else:
            verification = serializer.save(user=self.request.user)
            if profile_photo:
                from .services import save_account_profile_photo
                save_account_profile_photo(self.request.user, profile_photo)
            if verification.status == StudentVerification.Status.PENDING:
                from apps.notifications.staff import notify_staff_section
                notify_staff_section("verification", "Student verification needs review", f"{self.request.user.get_full_name().strip() or self.request.user.username} submitted student verification.", {"kind": "student_verification_review", "verification_id": verification.pk}, f"student-verification:{verification.pk}")
    def perform_destroy(self, instance):
        if instance.status != StudentVerification.Status.PENDING:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Reviewed verification submissions cannot be deleted.")
        if instance.student_id_image:
            from .services import delete_verification_id_image
            delete_verification_id_image(instance)
        instance.delete()

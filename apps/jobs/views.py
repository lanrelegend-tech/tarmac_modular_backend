import hashlib
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import MethodNotAllowed, PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.accounts.permissions import IsVerifiedStudent, is_student_feature_eligible
from apps.administration.models import AdminLog, PlatformSettings
from apps.administration.permissions import IsAdminOrAbove, IsModeratorOrAbove, PlatformFeatureEnabled
from apps.messaging.models import BlockedUser, Conversation
from apps.messaging.serializers import ConversationSerializer, create_participant_snapshot
from apps.notifications.email import send_tarmac_email
from apps.notifications.services import notify_user
from .models import JobCategory, JobEngagement, JobListing, JobReport
from .serializers import (
    AdminJobListingSerializer, AdminJobReportSerializer, ExternalJobSubmissionSerializer, JobEngagementSerializer,
    JobCategorySerializer, JobListingSerializer, JobReportSerializer,
)

logger = logging.getLogger(__name__)


def _token_digest(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _send_external_email(*, subject, body, recipient, url="", action_label="Open Tarmac"):
    try:
        send_tarmac_email(subject=subject, text_body=body, recipient=recipient, action_url=url,
                          action_label=action_label, preheader=subject)
    except Exception:
        logger.exception("Could not send Jobs & Gigs email")


def _notify_admins(subject, body, data, dedupe):
    for administrator in User.objects.filter(is_staff=True, is_active=True, is_suspended=False).iterator():
        notify_user(administrator, subject, body, data, f"{dedupe}:admin:{administrator.pk}")


class JobCategoryViewSet(viewsets.ModelViewSet):
    serializer_class = JobCategorySerializer
    queryset = JobCategory.objects.all()
    platform_feature = "jobs_enabled"

    def get_permissions(self):
        if self.request.user and self.request.user.is_staff:
            return [IsAdminOrAbove()]
        if self.action == "list":
            return [AllowAny(), PlatformFeatureEnabled()]
        return [IsAdminOrAbove()]

    def get_queryset(self):
        qs = JobCategory.objects.all()
        return qs if getattr(self.request.user, "is_staff", False) else qs.filter(is_active=True)

    def perform_destroy(self, instance):
        if instance.listings.exists():
            instance.is_active = False
            instance.save(update_fields=("is_active",))
        else:
            instance.delete()


class JobListingViewSet(viewsets.ModelViewSet):
    serializer_class = JobListingSerializer
    permission_classes = (IsVerifiedStudent, PlatformFeatureEnabled)
    platform_feature = "jobs_enabled"
    parser_classes = (JSONParser, FormParser, MultiPartParser)
    search_fields = ("title", "description", "requirements", "location", "business_name")
    ordering_fields = ("created_at", "deadline", "pay_min", "title")
    http_method_names = ("get", "post", "patch", "head", "options")

    def get_permissions(self):
        if self.request.user and self.request.user.is_staff:
            return [IsAdminOrAbove()]
        return [IsVerifiedStudent(), PlatformFeatureEnabled()]

    def get_queryset(self):
        today = timezone.localdate()
        JobListing.objects.filter(status=JobListing.Status.ACTIVE, deadline__lt=today).update(
            status=JobListing.Status.EXPIRED, closed_at=timezone.now()
        )
        qs = JobListing.objects.select_related("owner", "owner__course", "category", "anonymous_identity")
        if self.request.user.is_staff:
            return qs
        if self.request.query_params.get("mine") == "true":
            return qs.filter(source=JobListing.Source.STUDENT, owner=self.request.user)
        owner_public_id = self.request.query_params.get("owner_public_id")
        identity_type = self.request.query_params.get("identity_type", "normal")
        if owner_public_id:
            if identity_type == "anonymous":
                qs = qs.filter(identity_type="anonymous", anonymous_identity__public_id=owner_public_id, anonymous_identity__is_enabled=True, owner__is_active=True, owner__is_suspended=False, owner__is_student_verified=True)
            else:
                qs = qs.filter(identity_type="normal", owner__public_id=owner_public_id, owner__public_profile_enabled=True, owner__is_active=True, owner__is_suspended=False, owner__is_student_verified=True)
            return qs.filter(source=JobListing.Source.STUDENT, status=JobListing.Status.ACTIVE, deadline__gte=today, category__is_active=True)
        qs = qs.filter(source=JobListing.Source.BUSINESS, business_email_verified_at__isnull=False) | qs.filter(source=JobListing.Source.STUDENT)
        qs = qs.filter(status=JobListing.Status.ACTIVE, deadline__gte=today, category__is_active=True)
        qs = qs.filter(Q(identity_type="normal") | Q(identity_type="anonymous", anonymous_identity__is_enabled=True))
        shared_listing = self.request.query_params.get("listing")
        if shared_listing:
            if not shared_listing.isdigit():
                return qs.none()
            qs = qs.filter(pk=int(shared_listing))
        kind = self.request.query_params.get("kind")
        category = self.request.query_params.get("category")
        engagement = self.request.query_params.get("engagement")
        service_intent = self.request.query_params.get("service_intent")
        location = self.request.query_params.get("location")
        campus = self.request.query_params.get("campus")
        pay_min = self.request.query_params.get("pay_min")
        pay_max = self.request.query_params.get("pay_max")
        if kind in JobListing.Kind.values:
            qs = qs.filter(kind=kind)
        if service_intent in JobListing.ServiceIntent.values:
            qs = qs.filter(kind=JobListing.Kind.SERVICE, service_intent=service_intent)
        if category and category.isdigit():
            qs = qs.filter(category_id=int(category))
        if engagement in JobListing.Engagement.values:
            qs = qs.filter(engagement=engagement)
        if location:
            qs = qs.filter(location__icontains=location[:100])
        if campus:
            qs = qs.filter(campus__iexact=campus.strip()[:120])
        try:
            if pay_min:
                qs = qs.filter(Q(pay_max__gte=float(pay_min)) | Q(pay_max__isnull=True))
            if pay_max:
                qs = qs.filter(Q(pay_min__lte=float(pay_max)) | Q(pay_min__isnull=True))
        except ValueError:
            raise ValidationError({"pay": "Enter a valid pay amount."})
        ordering = self.request.query_params.get("ordering", "-created_at")
        if ordering in ("created_at", "-created_at", "deadline", "-deadline", "pay_min", "-pay_min", "title", "-title"):
            qs = qs.order_by(ordering)
        return qs.distinct()

    def perform_create(self, serializer):
        platform = PlatformSettings.get_solo()
        status_value = JobListing.Status.PENDING_REVIEW if platform.job_listing_approval_required else JobListing.Status.ACTIVE
        alias = serializer.validated_data.pop("_anonymous_identity", None)
        serializer.save(source=JobListing.Source.STUDENT, owner=self.request.user, anonymous_identity=alias, status=status_value)

    def perform_update(self, serializer):
        listing = serializer.instance
        if listing.owner_id != self.request.user.pk or listing.source != JobListing.Source.STUDENT:
            raise PermissionDenied("You can edit only your own student listing.")
        if listing.status not in (JobListing.Status.ACTIVE, JobListing.Status.PAUSED, JobListing.Status.PENDING_REVIEW):
            raise ValidationError({"status": "Closed listings cannot be edited."})
        new_status = JobListing.Status.PENDING_REVIEW if PlatformSettings.get_solo().job_listing_approval_required else JobListing.Status.ACTIVE
        alias = serializer.validated_data.pop("_anonymous_identity", listing.anonymous_identity if listing.identity_type == "anonymous" else None)
        serializer.save(anonymous_identity=alias, status=new_status, admin_note="", reviewed_by=None, reviewed_at=None)

    @action(detail=True, methods=("post",))
    def pause(self, request, pk=None):
        listing = self.get_object()
        if listing.owner_id != request.user.pk or listing.source != JobListing.Source.STUDENT:
            raise PermissionDenied("You can pause only your own student listing.")
        if listing.status != JobListing.Status.ACTIVE:
            raise ValidationError({"status": "Only an active listing can be paused."})
        listing.status = JobListing.Status.PAUSED
        listing.save(update_fields=("status", "updated_at"))
        return Response(self.get_serializer(listing).data)

    @action(detail=True, methods=("post",))
    def resume(self, request, pk=None):
        listing = self.get_object()
        if listing.owner_id != request.user.pk or listing.source != JobListing.Source.STUDENT:
            raise PermissionDenied("You can resume only your own student listing.")
        if listing.status != JobListing.Status.PAUSED or listing.deadline < timezone.localdate():
            raise ValidationError({"status": "This listing cannot be resumed. Check its status and deadline."})
        state = JobListing.Status.PENDING_REVIEW if PlatformSettings.get_solo().job_listing_approval_required else JobListing.Status.ACTIVE
        listing.status = state
        listing.save(update_fields=("status", "updated_at"))
        return Response(self.get_serializer(listing).data)

    @action(detail=True, methods=("post",))
    def filled(self, request, pk=None):
        listing = self.get_object()
        if listing.owner_id != request.user.pk or listing.source != JobListing.Source.STUDENT:
            raise PermissionDenied("You can close only your own student listing.")
        if listing.status not in (JobListing.Status.ACTIVE, JobListing.Status.PAUSED):
            raise ValidationError({"status": "This listing is already closed."})
        listing.status = JobListing.Status.FILLED
        listing.closed_at = timezone.now()
        listing.save(update_fields=("status", "closed_at", "updated_at"))
        return Response(self.get_serializer(listing).data)

    @action(detail=True, methods=("post",))
    def remove(self, request, pk=None):
        listing = self.get_object()
        if listing.owner_id != request.user.pk or listing.source != JobListing.Source.STUDENT:
            raise PermissionDenied("You can remove only your own student listing.")
        if listing.status in (JobListing.Status.REMOVED, JobListing.Status.FILLED, JobListing.Status.EXPIRED):
            raise ValidationError({"status": "This listing is already closed."})
        listing.status = JobListing.Status.REMOVED
        listing.closed_at = timezone.now()
        listing.save(update_fields=("status", "closed_at", "updated_at"))
        return Response(self.get_serializer(listing).data)

    @action(detail=True, methods=("post",))
    @transaction.atomic
    def message_poster(self, request, pk=None):
        listing = self.get_object()
        if listing.source != JobListing.Source.STUDENT or not listing.owner_id or listing.owner_id == request.user.pk:
            raise ValidationError("This listing does not have another student poster.")
        if not is_student_feature_eligible(listing.owner):
            raise ValidationError("This student cannot currently receive messages.")
        if BlockedUser.objects.filter(blocked_by__in=(request.user, listing.owner), blocked_user__in=(request.user, listing.owner)).exists():
            raise PermissionDenied("Messaging is unavailable because one account has blocked the other.")
        identity_type = request.data.get("identity_type", "normal")
        if identity_type not in ("normal", "anonymous"):
            raise ValidationError({"identity_type": "Choose normal or anonymous identity."})
        my_alias = getattr(request.user, "anonymous_marketplace_identity", None) if identity_type == "anonymous" else None
        if identity_type == "anonymous" and (not my_alias or not my_alias.is_enabled):
            raise ValidationError({"identity_type": "Create and enable your anonymous marketplace identity first."})
        target_type = listing.identity_type if listing.identity_type == "anonymous" and listing.anonymous_identity_id and listing.anonymous_identity.is_enabled else "normal"
        candidates = Conversation.objects.filter(context_type=Conversation.ContextType.JOBS, context_id=listing.pk, participants=request.user).filter(participants=listing.owner).prefetch_related("participant_snapshots")
        my_public_id = str(my_alias.public_id) if my_alias else str(request.user.public_id)
        target_public_id = str(listing.anonymous_identity.public_id) if target_type == "anonymous" else str(listing.owner.public_id)
        conversation = next((row for row in candidates if (snap := next((s for s in row.participant_snapshots.all() if s.user_id == request.user.pk), None)) and snap.identity_type == identity_type and snap.public_identity_id == my_public_id and (target := next((s for s in row.participant_snapshots.all() if s.user_id == listing.owner_id), None)) and target.identity_type == target_type and target.public_identity_id == target_public_id), None)
        if not conversation:
            conversation = Conversation.objects.create(context_type=Conversation.ContextType.JOBS, context_id=listing.pk, context_title=listing.title)
            conversation.participants.add(request.user, listing.owner)
            create_participant_snapshot(conversation, request.user, identity_type=identity_type)
            create_participant_snapshot(conversation, listing.owner, identity_type=target_type)
        return Response(ConversationSerializer(conversation, context={"request": request}).data, status=status.HTTP_201_CREATED)


class JobEngagementViewSet(viewsets.ModelViewSet):
    serializer_class = JobEngagementSerializer
    permission_classes = (IsVerifiedStudent, PlatformFeatureEnabled)
    platform_feature = "jobs_enabled"
    http_method_names = ("get", "post", "head", "options")

    def get_queryset(self):
        queryset = JobEngagement.objects.filter(Q(poster=self.request.user) | Q(applicant=self.request.user)).select_related("listing", "conversation")
        conversation_id = self.request.query_params.get("conversation")
        if conversation_id and conversation_id.isdigit():
            queryset = queryset.filter(conversation_id=int(conversation_id))
        return queryset

    @transaction.atomic
    def perform_create(self, serializer):
        conversation = serializer.validated_data["conversation"]
        people = list(conversation.participants.filter(is_staff=False))
        listing = JobListing.objects.select_for_update().filter(pk=conversation.context_id, status=JobListing.Status.ACTIVE, source=JobListing.Source.STUDENT).first()
        if conversation.context_type != Conversation.ContextType.JOBS or not listing or len(people) != 2 or listing.owner_id not in {p.pk for p in people}:
            raise ValidationError({"conversation": "Choose a valid Jobs & Gigs conversation and listing."})
        if not all(is_student_feature_eligible(person) for person in people):
            raise PermissionDenied("Both participants must have active verified accounts.")
        if JobEngagement.objects.filter(conversation=conversation).exists():
            raise ValidationError({"conversation": "An engagement record already exists for this conversation."})
        applicant = next(person for person in people if person.pk != listing.owner_id)
        engagement = serializer.save(listing=listing, poster_id=listing.owner_id, applicant=applicant, initiated_by=self.request.user, status=JobEngagement.Status.OFFERED)
        target = applicant if self.request.user.pk == listing.owner_id else listing.owner
        notify_user(target, "Job or gig engagement request", f"A participant proposed tracking completion for “{listing.title}”. Review it in your Tarmac messages.", {"kind": "job_engagement", "engagement_id": engagement.pk}, f"job-engagement:{engagement.pk}:offered")

    def _notify_other(self, engagement, title, body, suffix):
        target = engagement.applicant if self.request.user.pk == engagement.poster_id else engagement.poster
        notify_user(target, title, body, {"kind": "job_engagement", "engagement_id": engagement.pk}, f"job-engagement:{engagement.pk}:{suffix}")

    @action(detail=True, methods=("post",))
    @transaction.atomic
    def accept(self, request, pk=None):
        engagement = JobEngagement.objects.select_for_update().get(pk=self.get_object().pk)
        if request.user.pk == engagement.initiated_by_id or engagement.status != JobEngagement.Status.OFFERED:
            raise ValidationError("Only the other participant can accept a pending engagement request.")
        engagement.status = JobEngagement.Status.ACTIVE
        engagement.accepted_at = timezone.now()
        engagement.save(update_fields=("status", "accepted_at", "updated_at"))
        self._notify_other(engagement, "Job or gig engagement accepted", "Both participants can now confirm completion after the work is done.", "accepted")
        return Response(self.get_serializer(engagement).data)

    @action(detail=True, methods=("post",), url_path="request-completion")
    @transaction.atomic
    def request_completion(self, request, pk=None):
        engagement = JobEngagement.objects.select_for_update().get(pk=self.get_object().pk)
        if engagement.status != JobEngagement.Status.ACTIVE:
            raise ValidationError("Only an active engagement can be marked for completion.")
        engagement.status = JobEngagement.Status.COMPLETION_REQUESTED
        engagement.completion_requested_by = request.user
        engagement.save(update_fields=("status", "completion_requested_by", "updated_at"))
        self._notify_other(engagement, "Confirm job or gig completion", "The other participant says this engagement is complete. Confirm only if you agree.", "completion-requested")
        return Response(self.get_serializer(engagement).data)

    @action(detail=True, methods=("post",), url_path="confirm-completion")
    @transaction.atomic
    def confirm_completion(self, request, pk=None):
        engagement = JobEngagement.objects.select_for_update().get(pk=self.get_object().pk)
        if engagement.status != JobEngagement.Status.COMPLETION_REQUESTED or request.user.pk == engagement.completion_requested_by_id:
            raise ValidationError("Only the other participant can confirm a pending completion request.")
        engagement.status = JobEngagement.Status.COMPLETED
        engagement.completed_at = timezone.now()
        engagement.save(update_fields=("status", "completed_at", "updated_at"))
        self._notify_other(engagement, "Job or gig marked complete", "Both participants confirmed completion. You can now leave a Jobs & Gigs review.", "completed")
        return Response(self.get_serializer(engagement).data)

    @action(detail=True, methods=("post",))
    @transaction.atomic
    def cancel(self, request, pk=None):
        engagement = JobEngagement.objects.select_for_update().get(pk=self.get_object().pk)
        if engagement.status in (JobEngagement.Status.COMPLETED, JobEngagement.Status.CANCELLED):
            raise ValidationError("This engagement is already closed.")
        engagement.status = JobEngagement.Status.CANCELLED
        engagement.cancelled_at = timezone.now()
        engagement.save(update_fields=("status", "cancelled_at", "updated_at"))
        self._notify_other(engagement, "Job or gig engagement cancelled", "The engagement tracking request was cancelled.", "cancelled")
        return Response(self.get_serializer(engagement).data)


class JobReportViewSet(viewsets.ModelViewSet):
    serializer_class = JobReportSerializer
    permission_classes = (IsVerifiedStudent, PlatformFeatureEnabled)
    platform_feature = "jobs_enabled"
    http_method_names = ("get", "post", "head", "options")

    def get_queryset(self):
        return JobReport.objects.filter(reporter=self.request.user).select_related("listing")

    def perform_create(self, serializer):
        report = serializer.save(reporter=self.request.user)
        _notify_admins("New Jobs & Gigs report", f"A student reported listing #{report.listing_id}.", {"kind": "job_report", "report_id": report.pk}, f"job-report:{report.pk}")


class ExternalJobSubmissionView(APIView):
    authentication_classes = []
    permission_classes = (AllowAny,)
    throttle_classes = (ScopedRateThrottle,)
    throttle_scope = "external_job_submission"

    def post(self, request):
        platform = PlatformSettings.get_solo()
        if not platform.jobs_enabled or not platform.external_job_submissions_enabled:
            return Response({"detail": "External job submissions are currently closed."}, status=status.HTTP_403_FORBIDDEN)
        if str(request.data.get("website_confirm", "")).strip():
            return Response({"detail": "Check the business email address for a confirmation link."}, status=status.HTTP_201_CREATED)
        serializer = ExternalJobSubmissionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        token = secrets.token_urlsafe(32)
        expires = timezone.now() + timedelta(hours=24)
        listing = serializer.save(
            source=JobListing.Source.BUSINESS, owner=None, status=JobListing.Status.PENDING_EMAIL,
            business_email_token_hash=_token_digest(token), business_email_token_expires_at=expires,
        )
        frontend_origin = settings.FRONTEND_ORIGIN
        verify_url = f"{frontend_origin}/jobs/verify-submission#{token}"
        _send_external_email(
            subject="Confirm your Tarmac job submission",
            body=f"We received the job opportunity for {listing.business_name}. Confirm this email address within 24 hours so the school can review the submission. If you did not submit it, ignore this message.",
            recipient=listing.business_email, url=verify_url, action_label="Confirm email",
        )
        return Response({"detail": "Check the business email address for a confirmation link. The listing will go to admin review after confirmation."}, status=status.HTTP_201_CREATED)


class ExternalJobEmailConfirmView(APIView):
    authentication_classes = []
    permission_classes = (AllowAny,)
    throttle_classes = (ScopedRateThrottle,)
    throttle_scope = "external_job_submission"

    @transaction.atomic
    def post(self, request):
        token = str(request.data.get("token", "")).strip()
        if len(token) < 32:
            raise ValidationError({"token": "This confirmation link is invalid or expired."})
        listing = JobListing.objects.select_for_update().filter(
            source=JobListing.Source.BUSINESS,
            business_email_token_hash=_token_digest(token),
            business_email_token_expires_at__gt=timezone.now(),
            status=JobListing.Status.PENDING_EMAIL,
        ).first()
        if not listing:
            raise ValidationError({"token": "This confirmation link is invalid, expired, or already used."})
        listing.business_email_verified_at = timezone.now()
        listing.business_email_token_hash = ""
        listing.business_email_token_expires_at = None
        listing.status = JobListing.Status.PENDING_REVIEW
        listing.save(update_fields=("business_email_verified_at", "business_email_token_hash", "business_email_token_expires_at", "status", "updated_at"))
        _notify_admins("External job awaiting review", f"{listing.business_name} submitted {listing.title}.", {"kind": "external_job_submission", "listing_id": listing.pk}, f"external-job:{listing.pk}")
        _send_external_email(subject="Your Tarmac submission is under review", body=f"Your email is confirmed. The KWASU team will review {listing.title} before it is published.", recipient=listing.business_email)
        return Response({"detail": "Email confirmed. Your opportunity is waiting for admin review."})


class ExternalJobResendEmailView(APIView):
    authentication_classes = []
    permission_classes = (AllowAny,)
    throttle_classes = (ScopedRateThrottle,)
    throttle_scope = "external_job_submission"

    def post(self, request):
        email = str(request.data.get("email", "")).strip().lower()
        from django.core.validators import validate_email
        from django.core.exceptions import ValidationError as DjangoValidationError
        try:
            validate_email(email)
        except DjangoValidationError:
            return Response({"detail": "If a submission needs email confirmation, we sent a new link."})
        listing = JobListing.objects.filter(source=JobListing.Source.BUSINESS, business_email__iexact=email, status=JobListing.Status.PENDING_EMAIL).order_by("-created_at").first()
        if listing:
            token = secrets.token_urlsafe(32)
            listing.business_email_token_hash = _token_digest(token)
            listing.business_email_token_expires_at = timezone.now() + timedelta(hours=24)
            listing.save(update_fields=("business_email_token_hash", "business_email_token_expires_at", "updated_at"))
            origin = settings.FRONTEND_ORIGIN
            _send_external_email(subject="Confirm your Tarmac job submission", body="Use the secure link to confirm the business email for your job submission. This link expires in 24 hours.", recipient=listing.business_email, url=f"{origin}/jobs/verify-submission#{token}", action_label="Confirm email")
        return Response({"detail": "If a submission needs email confirmation, we sent a new link."})


class ExternalJobManageView(APIView):
    authentication_classes = []
    permission_classes = (AllowAny,)
    throttle_classes = (ScopedRateThrottle,)
    throttle_scope = "external_job_manage"

    def post(self, request):
        token = str(request.data.get("token", "")).strip()
        action_name = str(request.data.get("action", "details")).strip()
        if len(token) < 32:
            raise ValidationError({"token": "This management link is invalid or expired."})
        listing = JobListing.objects.filter(
            source=JobListing.Source.BUSINESS, management_token_hash=_token_digest(token),
            management_token_revoked_at__isnull=True, management_token_expires_at__gt=timezone.now(),
        ).first()
        if not listing:
            raise ValidationError({"token": "This management link is invalid, expired, or revoked."})
        listing.management_last_used_at = timezone.now()
        listing.save(update_fields=("management_last_used_at",))
        if action_name == "details":
            return Response({"title": listing.title, "business_name": listing.business_name, "status": listing.status, "deadline": listing.deadline, "admin_note": listing.admin_note})
        if action_name == "pause" and listing.status == JobListing.Status.ACTIVE:
            listing.status = JobListing.Status.PAUSED
        elif action_name == "resume" and listing.status == JobListing.Status.PAUSED and listing.deadline >= timezone.localdate():
            listing.status = JobListing.Status.ACTIVE
        elif action_name == "filled" and listing.status in (JobListing.Status.ACTIVE, JobListing.Status.PAUSED):
            listing.status = JobListing.Status.FILLED
            listing.closed_at = timezone.now()
        elif action_name == "delete" and listing.status not in (JobListing.Status.REMOVED, JobListing.Status.DELETION_REQUESTED):
            listing.status = JobListing.Status.DELETION_REQUESTED
        else:
            raise ValidationError({"action": "This action is not available for the current listing status."})
        listing.save(update_fields=("status", "closed_at", "updated_at"))
        _notify_admins("External job listing updated", f"The business management link changed {listing.title} to {listing.get_status_display()}.", {"kind": "external_job_updated", "listing_id": listing.pk}, f"external-job-update:{listing.pk}:{listing.status}")
        return Response({"title": listing.title, "status": listing.status, "deadline": listing.deadline})


class AdminJobListingViewSet(viewsets.ModelViewSet):
    queryset = JobListing.objects.select_related("owner", "category", "reviewed_by").all()
    serializer_class = AdminJobListingSerializer
    permission_classes = (IsModeratorOrAbove,)
    http_method_names = ("get", "post", "patch", "head", "options")
    search_fields = ("title", "business_name", "business_email", "owner__email")
    filterset_fields = ("source", "kind", "status")

    def create(self, request, *args, **kwargs):
        raise MethodNotAllowed("POST", detail="Listings are submitted by students or through the external business submission form.")

    @action(detail=True, methods=("post",))
    @transaction.atomic
    def approve(self, request, pk=None):
        listing = self.get_object()
        if listing.status != JobListing.Status.PENDING_REVIEW:
            raise ValidationError({"status": "Only listings pending review can be approved."})
        if listing.deadline < timezone.localdate():
            raise ValidationError({"deadline": "This listing deadline has passed."})
        listing.status = JobListing.Status.ACTIVE
        listing.reviewed_by = request.user
        listing.reviewed_at = timezone.now()
        listing.admin_note = str(request.data.get("admin_note", "")).strip()[:3000]
        if listing.source == JobListing.Source.BUSINESS:
            raw_token = secrets.token_urlsafe(40)
            listing.management_token_hash = _token_digest(raw_token)
            listing.management_token_expires_at = min(timezone.now() + timedelta(days=90), timezone.make_aware(timezone.datetime.combine(listing.deadline, timezone.datetime.max.time())))
        listing.save(update_fields=("status", "reviewed_by", "reviewed_at", "admin_note", "management_token_hash", "management_token_expires_at", "updated_at"))
        AdminLog.objects.create(actor=request.user, action="job_listing_approved", target_type="JobListing", target_id=str(listing.pk), metadata={"source": listing.source})
        if listing.source == JobListing.Source.BUSINESS:
            frontend_origin = settings.FRONTEND_ORIGIN
            manage_url = f"{frontend_origin}/jobs/manage#{raw_token}"
            _send_external_email(subject="Your Tarmac job opportunity is approved", body=f"Your opportunity, {listing.title}, has been approved and is now visible to verified KWASU students. Use your private link to pause it, mark it filled, or request removal. Keep this link private.", recipient=listing.business_email, url=manage_url, action_label="Manage your listing")
        elif listing.owner_id:
            notify_user(listing.owner, "Your Jobs & Gigs listing is approved", f"{listing.title} is now visible to verified students.", {"kind": "job_listing_review", "listing_id": listing.pk, "status": listing.status}, f"job-approved:{listing.pk}")
        return Response(self.get_serializer(listing).data)

    @action(detail=True, methods=("post",))
    def reject(self, request, pk=None):
        listing = self.get_object()
        if listing.status != JobListing.Status.PENDING_REVIEW:
            raise ValidationError({"status": "Only listings pending review can be rejected."})
        admin_note = str(request.data.get("admin_note", "")).strip()[:3000]
        if len(admin_note) < 5:
            raise ValidationError({"admin_note": "Give the student or business a clear reason (at least 5 characters)."})
        listing.status = JobListing.Status.REJECTED
        listing.reviewed_by = request.user
        listing.reviewed_at = timezone.now()
        listing.admin_note = admin_note
        listing.save(update_fields=("status", "reviewed_by", "reviewed_at", "admin_note", "updated_at"))
        AdminLog.objects.create(actor=request.user, action="job_listing_rejected", target_type="JobListing", target_id=str(listing.pk), metadata={"source": listing.source, "note": listing.admin_note})
        if listing.source == JobListing.Source.BUSINESS:
            _send_external_email(subject="Your Tarmac job submission needs changes", body=f"The KWASU team could not approve {listing.title}. Review the note from the team and contact Tarmac support if you need help.\n\n{listing.admin_note}", recipient=listing.business_email)
        elif listing.owner_id:
            notify_user(listing.owner, "Your Jobs & Gigs listing needs changes", listing.admin_note or "Your listing was not approved. Contact Tarmac support for details.", {"kind": "job_listing_review", "listing_id": listing.pk, "status": listing.status}, f"job-rejected:{listing.pk}")
        return Response(self.get_serializer(listing).data)

    @action(detail=True, methods=("post",))
    def remove(self, request, pk=None):
        listing = self.get_object()
        admin_note = str(request.data.get("admin_note", "")).strip()[:3000]
        if len(admin_note) < 5:
            raise ValidationError({"admin_note": "Give a clear reason for removing this listing (at least 5 characters)."})
        listing.status = JobListing.Status.REMOVED
        listing.closed_at = timezone.now()
        listing.management_token_revoked_at = timezone.now() if listing.source == JobListing.Source.BUSINESS else None
        listing.admin_note = admin_note
        listing.save(update_fields=("status", "closed_at", "management_token_revoked_at", "admin_note", "updated_at"))
        AdminLog.objects.create(actor=request.user, action="job_listing_removed", target_type="JobListing", target_id=str(listing.pk), metadata={"source": listing.source})
        if listing.source == JobListing.Source.BUSINESS and listing.business_email_verified_at:
            _send_external_email(
                subject="Your Tarmac job opportunity was removed",
                body=f"The KWASU team removed your opportunity, {listing.title}. Reason: {listing.admin_note}",
                recipient=listing.business_email,
            )
        elif listing.owner_id:
            notify_user(listing.owner, "Your Jobs & Gigs listing was removed", listing.admin_note, {"kind": "job_listing_review", "listing_id": listing.pk, "status": listing.status}, f"job-removed:{listing.pk}")
        return Response(self.get_serializer(listing).data)

    @action(detail=True, methods=("post",), url_path="rotate-management-link")
    def rotate_management_link(self, request, pk=None):
        listing = self.get_object()
        if listing.source != JobListing.Source.BUSINESS or not listing.business_email_verified_at or listing.status not in (JobListing.Status.ACTIVE, JobListing.Status.PAUSED):
            raise ValidationError("Only an active or paused verified business listing can receive a renewed management link.")
        raw_token = secrets.token_urlsafe(40)
        listing.management_token_hash = _token_digest(raw_token)
        listing.management_token_expires_at = min(timezone.now() + timedelta(days=90), timezone.make_aware(timezone.datetime.combine(listing.deadline, timezone.datetime.max.time())))
        listing.management_token_revoked_at = None
        listing.save(update_fields=("management_token_hash", "management_token_expires_at", "management_token_revoked_at", "updated_at"))
        origin = settings.FRONTEND_ORIGIN
        _send_external_email(subject="Your Tarmac job management link was renewed", body=f"A new private management link was created for {listing.title}. The old link no longer works.", recipient=listing.business_email, url=f"{origin}/jobs/manage#{raw_token}", action_label="Manage your listing")
        AdminLog.objects.create(actor=request.user, action="external_job_management_link_rotated", target_type="JobListing", target_id=str(listing.pk), metadata={})
        return Response({"detail": "A new management link was emailed to the verified business address."})


class AdminJobReportViewSet(viewsets.ModelViewSet):
    queryset = JobReport.objects.select_related("listing", "reporter").all()
    serializer_class = AdminJobReportSerializer
    permission_classes = (IsModeratorOrAbove,)
    http_method_names = ("get", "patch", "head", "options")
    filterset_fields = ("status", "reason")

    def perform_update(self, serializer):
        report = serializer.save()
        if report.status in (JobReport.Status.RESOLVED, JobReport.Status.DISMISSED):
            report.resolved_at = timezone.now()
            report.save(update_fields=("resolved_at",))
        AdminLog.objects.create(actor=self.request.user, action=f"job_report_{report.status}", target_type="JobReport", target_id=str(report.pk), metadata={"listing_id": report.listing_id})

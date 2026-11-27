from datetime import timedelta
from uuid import UUID
import hashlib
import os
import secrets
import uuid
from urllib.parse import urlencode
from django.db import transaction
from django.core.cache import cache
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from rest_framework import viewsets,permissions, status, generics
from rest_framework.decorators import action, api_view, permission_classes as drf_permission_classes
from rest_framework.exceptions import APIException, MethodNotAllowed, ValidationError
from rest_framework.response import Response
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from django.db import models
from django.db.models import Count, Sum
from django.db.models.functions import TruncDate
from django.db.models import Prefetch
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from apps.accounts.permissions import IsVerifiedStudent
from apps.accounts.permissions import is_student_feature_eligible
from apps.accounts.models import User, ProfileChangeRequest, ProfileChangeMessage, CourseProgram, AccountActionToken
from apps.accounts.models import StudentVerification
from apps.reviews.models import ReviewReport, SellerReview
from apps.products.models import Product
from apps.payments.models import Payment
from apps.lost_found.models import LostFoundReport, ItemMatch, ItemClaim
from apps.lost_found.services import create_possible_matches
from apps.ads.models import AdCampaign, AdWaitlistEntry
from apps.jobs.models import JobListing, JobReport
from apps.accommodation.models import AccommodationListing, HostelAgentRequest
from apps.messaging.models import Conversation, Message
from apps.messaging.serializers import MessageSerializer
from .models import UserReport,ReportEvidence,Announcement,AnnouncementBookmark,AnnouncementPublisher,AdminLog,PlatformSettings,SupportTicket,SupportMessage,SupportAttachment,SubscriptionPlan,StudentSubscription,EmergencyContact
from .permissions import IsAdminOrAbove, IsAnnouncementPublisherOrModerator, IsModeratorOrAbove, has_announcement_publisher_access
from .serializers import UserReportSerializer,AnnouncementSerializer,AnnouncementPublisherSerializer,AdminLogSerializer,AdminVerificationSerializer,AdminReviewSerializer,AdminReviewReportSerializer,AdminAccountSerializer,AdminAdministratorSerializer,AdminConversationSerializer,SubscriptionPlanSerializer,StudentSubscriptionSerializer,SupportTicketSerializer,PlatformSettingsSerializer,AdminPaymentSerializer,AdminLostFoundSerializer,AdminItemClaimSerializer,AdminItemMatchSerializer,AdminProductSerializer,EmergencyContactSerializer
from apps.accounts.serializers import AdminProfileChangeRequestSerializer, ProfileUpdateSerializer
from apps.notifications.services import notify_user
from config.storage import optimized_image_response
from config.throttles import ActionRateLimitMixin
import logging

logger = logging.getLogger(__name__)

class UserReportViewSet(ActionRateLimitMixin, viewsets.ModelViewSet):
    action_throttle_scopes = {"create": "user_report_create"}
    serializer_class=UserReportSerializer
    http_method_names = ["get", "post", "patch", "head", "options"]
    filterset_fields = ["is_resolved", "reason"]

    def get_permissions(self):
        if self.request.user and self.request.user.is_staff:
            return [IsModeratorOrAbove()]
        return [IsVerifiedStudent()]

    def get_queryset(self):
        queryset = UserReport.objects.select_related("reporter", "reported_user", "product", "lost_found_report").prefetch_related("evidence")
        return queryset if self.request.user.is_staff else queryset.filter(reporter=self.request.user)

    def perform_create(self,serializer):
        report = serializer.save(reporter=self.request.user)
        if PlatformSettings.get_solo().report_notifications_enabled:
            from apps.notifications.staff import notify_staff_section
            notify_staff_section("reports", "New student report", f"A student submitted report #{report.pk}. Open the admin report queue to review it.", {"kind":"user_report", "report_id":report.pk}, f"user-report:{report.pk}")

    def perform_update(self, serializer):
        if not self.request.user.is_staff:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Only school administrators can update a report.")
        report = serializer.save()
        if report.status == UserReport.Status.INVESTIGATING and report.assigned_to_id is None:
            report.assigned_to = self.request.user
        if report.status in (UserReport.Status.RESOLVED, UserReport.Status.DISMISSED):
            report.is_resolved = True
        else:
            report.is_resolved = False
        report.save(update_fields=("is_resolved",))
        AdminLog.objects.create(actor=self.request.user, action=f"user_report_{report.status}", target_type="UserReport", target_id=str(report.pk), metadata={"reviewer_note": report.reviewer_note})
    @action(detail=True, methods=("post",))
    def warn(self, request, pk=None):
        report = self.get_object()
        if not report.reported_user_id:
            raise ValidationError({"reported_user": "This report is not associated with a student account."})
        note = str(request.data.get("note", "")).strip()
        if len(note) < 5:
            raise ValidationError({"note": "Add a short explanation for the warning."})
        notify_user(report.reported_user, "Official Tarmac warning", note, {"kind": "admin_warning", "report_id": report.pk}, f"admin-warning:{report.pk}")
        AdminLog.objects.create(actor=request.user, action="user_warning_issued", target_type="User", target_id=str(report.reported_user_id), metadata={"report_id": report.pk, "note": note})
        return Response({"warned": True})
class AnnouncementViewSet(ActionRateLimitMixin, viewsets.ModelViewSet):
    action_throttle_scopes = {
        "create": "announcement_write",
        "update": "announcement_write",
        "partial_update": "announcement_write",
        "publish": "announcement_write",
        "toggle_saved": "favourite_write",
    }
    serializer_class=AnnouncementSerializer
    def get_queryset(self):
        if has_announcement_publisher_access(self.request.user):
            queryset = Announcement.objects.filter(created_by=self.request.user).select_related("created_by").prefetch_related("gallery_images").order_by("-created_at")
        elif self.request.user.is_staff:
            queryset = Announcement.objects.select_related("created_by").prefetch_related("gallery_images").all()
        elif not PlatformSettings.get_solo().announcements_enabled:
            queryset = Announcement.objects.none()
        else:
            queryset = Announcement.objects.filter(is_published=True, is_archived=False).filter(
                models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=timezone.now())
            ).filter(
                models.Q(audience=Announcement.Audience.ALL_VERIFIED) |
                models.Q(audience=Announcement.Audience.DEPARTMENT, department__iexact=self.request.user.department)
            ).select_related("created_by").prefetch_related("gallery_images").order_by("-published_at", "-created_at")

        # Keep date/time filtering in SQL so it works across paginated results.
        after_value = self.request.query_params.get("published_after")
        before_value = self.request.query_params.get("published_before")
        after = parse_datetime(after_value) if after_value else None
        before = parse_datetime(before_value) if before_value else None
        if (after_value and after is None) or (before_value and before is None):
            raise ValidationError({"published_at": "Use valid date and time values."})
        if after and timezone.is_naive(after):
            after = timezone.make_aware(after, timezone.get_current_timezone())
        if before and timezone.is_naive(before):
            before = timezone.make_aware(before, timezone.get_current_timezone())
        if after and before and after > before:
            raise ValidationError({"published_at": "The start date and time must be before the end date and time."})
        if after:
            queryset = queryset.filter(published_at__gte=after)
        if before:
            queryset = queryset.filter(published_at__lte=before)
        return queryset
    def get_permissions(self):
        if self.action in ("saved", "toggle_saved"):
            return [IsVerifiedStudent()]
        if self.action in ("list", "retrieve"):
            if self.request.user and self.request.user.is_staff:
                return [IsModeratorOrAbove()]
            if has_announcement_publisher_access(self.request.user):
                return [IsAnnouncementPublisherOrModerator()]
            return [IsVerifiedStudent()]
        if self.action == "pin":
            return [IsModeratorOrAbove()]
        return [IsAnnouncementPublisherOrModerator()]
    @action(detail=False, methods=["get"])
    def saved(self, request):
        ids = AnnouncementBookmark.objects.filter(
            user=request.user,
            announcement__is_published=True,
            announcement__is_archived=False,
        ).filter(models.Q(announcement__expires_at__isnull=True) | models.Q(announcement__expires_at__gt=timezone.now())).values_list("announcement_id", flat=True)
        return Response({"ids": list(ids)})
    @action(detail=True, methods=["post"], url_path="toggle-saved")
    def toggle_saved(self, request, pk=None):
        announcement = self.get_object()
        bookmark, created = AnnouncementBookmark.objects.get_or_create(user=request.user, announcement=announcement)
        if not created:
            bookmark.delete()
        return Response({"saved": created, "announcement_id": announcement.pk})
    def perform_create(self, serializer):
        audience = serializer.validated_data.get("audience", Announcement.Audience.ALL_VERIFIED)
        if has_announcement_publisher_access(self.request.user):
            if audience != Announcement.Audience.ALL_VERIFIED:
                raise ValidationError({"audience": "Announcement publishers can post to all verified students only."})
            if serializer.validated_data.get("pinned", False):
                raise ValidationError({"pinned": "Only Tarmac moderators can pin announcements."})
        if audience == Announcement.Audience.DEPARTMENT and not serializer.validated_data.get("department", "").strip():
            raise ValidationError({"department": "Choose a department for a department announcement."})
        announcement = serializer.save(created_by=self.request.user)
        AdminLog.objects.create(actor=self.request.user, action="announcement_created", target_type="Announcement", target_id=str(announcement.pk), metadata={"title": announcement.title, "audience": announcement.audience})
    def perform_update(self, serializer):
        if has_announcement_publisher_access(self.request.user):
            audience = serializer.validated_data.get("audience", serializer.instance.audience)
            pinned = serializer.validated_data.get("pinned", serializer.instance.pinned)
            if audience != Announcement.Audience.ALL_VERIFIED:
                raise ValidationError({"audience": "Announcement publishers can post to all verified students only."})
            if pinned:
                raise ValidationError({"pinned": "Only Tarmac moderators can pin announcements."})
        announcement = serializer.save()
        AdminLog.objects.create(actor=self.request.user, action="announcement_updated", target_type="Announcement", target_id=str(announcement.pk), metadata={"title": announcement.title})
    def perform_destroy(self, instance):
        if not instance.is_archived:
            instance.is_archived = True
            instance.is_published = False
            instance.pinned = False
            instance.archived_at = timezone.now()
            instance.save(update_fields=("is_archived", "is_published", "pinned", "archived_at"))
            AdminLog.objects.create(actor=self.request.user, action="announcement_archived", target_type="Announcement", target_id=str(instance.pk), metadata={"title": instance.title, "was_published": bool(instance.published_at)})
    @action(detail=True, methods=["post"])
    def publish(self, request, pk=None):
        if not PlatformSettings.get_solo().announcements_enabled:
            raise ValidationError("Announcements are currently disabled in platform settings.")
        announcement = self.get_object()
        if announcement.is_archived:
            raise ValidationError("Restore this archived announcement before publishing it again.")
        if announcement.expires_at and announcement.expires_at <= timezone.now():
            raise ValidationError("Choose an expiry date in the future before publishing this announcement.")
        if announcement.is_published:
            return Response({"detail": "This announcement has already been published."}, status=status.HTTP_409_CONFLICT)
        now = timezone.now()
        announcement.is_published = True
        announcement.published_at = now
        announcement.save(update_fields=("is_published", "published_at"))
        AdminLog.objects.create(actor=request.user, action="announcement_published", target_type="Announcement", target_id=str(announcement.pk), metadata={"audience":announcement.audience,"department":announcement.department})
        return Response({"published": True, "delivery_queued": True})
    @action(detail=True, methods=["post"])
    def restore(self, request, pk=None):
        announcement = self.get_object()
        if announcement.is_archived:
            announcement.is_archived = False
            announcement.archived_at = None
            announcement.save(update_fields=("is_archived", "archived_at"))
            AdminLog.objects.create(actor=request.user, action="announcement_restored", target_type="Announcement", target_id=str(announcement.pk), metadata={})
        return Response({"restored": True, "published": announcement.is_published})
    @action(detail=True, methods=["patch"])
    def pin(self, request, pk=None):
        announcement = self.get_object()
        announcement.pinned = bool(request.data.get("pinned", not announcement.pinned))
        announcement.save(update_fields=("pinned",))
        AdminLog.objects.create(actor=request.user, action="announcement_pinned" if announcement.pinned else "announcement_unpinned", target_type="Announcement", target_id=str(announcement.pk), metadata={})
        return Response({"pinned": announcement.pinned})
class AdminLogViewSet(viewsets.ReadOnlyModelViewSet): queryset=AdminLog.objects.select_related("actor").all(); serializer_class=AdminLogSerializer; permission_classes=[IsAdminOrAbove]

@api_view(["GET"])
@drf_permission_classes([permissions.IsAuthenticated])
def report_evidence_image(request, evidence_id):
    evidence = get_object_or_404(ReportEvidence.objects.select_related("report"), pk=evidence_id)
    if not request.user.is_staff and evidence.report.reporter_id != request.user.pk:
        from rest_framework.exceptions import PermissionDenied
        raise PermissionDenied("You cannot view evidence attached to another student's report.")
    return optimized_image_response(evidence.image)

class AdminVerificationViewSet(viewsets.ModelViewSet):
    serializer_class = AdminVerificationSerializer
    permission_classes = [IsModeratorOrAbove]
    http_method_names = ["get", "patch", "head", "options"]
    search_fields = ["user__email", "user__username", "user__first_name", "user__last_name", "user__matric_number", "user__anonymous_marketplace_identity__handle"]

    def get_queryset(self):
        # Keep the newest submissions visible on the first admin page. Ordering
        # by status first could bury a newly approved student's record behind
        # older records and pagination.
        queryset = StudentVerification.objects.select_related("user", "user__course", "user__anonymous_marketplace_identity", "reviewed_by").order_by("-created_at", "-pk")
        status_filter = self.request.query_params.get("status")
        return queryset.filter(status=status_filter) if status_filter else queryset

    def filter_queryset(self, queryset):
        queryset = super().filter_queryset(queryset)
        term = str(self.request.query_params.get("search", "")).strip()
        if term:
            try:
                public_id = UUID(term)
            except (ValueError, TypeError, AttributeError):
                return queryset
            return queryset | self.get_queryset().filter(user__public_id=public_id)
        return queryset

    @transaction.atomic
    def perform_update(self, serializer):
        verification = StudentVerification.objects.select_for_update().select_related("user").get(pk=serializer.instance.pk)
        serializer.instance = verification
        if verification.status != StudentVerification.Status.PENDING:
            raise ValidationError({"status": "This verification request has already been reviewed."})
        status_value = serializer.validated_data["status"]
        user = User.objects.select_for_update().get(pk=verification.user_id)
        if status_value == StudentVerification.Status.APPROVED:
            platform = PlatformSettings.get_solo()
            if platform.email_verification_required and not user.email_verified:
                raise ValidationError({"status": "The student must verify their email before approval."})
            if platform.require_student_id and not verification.student_id_image:
                raise ValidationError({"status": "The student has not submitted an ID image. Ask them to submit it before approval."})
            if not user.profile_picture:
                raise ValidationError({"status": "The student has not submitted a profile photo for account security."})
            duplicate = User.objects.filter(matric_number=verification.submitted_matric_number).exclude(pk=user.pk).exists()
            if duplicate:
                raise ValidationError({"status": "This matric number is already assigned to another account."})
            user.matric_number = verification.submitted_matric_number
            user.is_student_verified = True
            user.verified_affiliation = verification.requested_affiliation
            user.student_status_override = None
            user.save(update_fields=("matric_number", "is_student_verified", "verified_affiliation", "student_status_override"))
        else:
            user.is_student_verified = False
            user.verified_affiliation = None
            user.student_status_override = None
            user.save(update_fields=("is_student_verified", "verified_affiliation", "student_status_override"))
        verification.status = status_value
        verification.reviewer_note = serializer.validated_data.get("reviewer_note", verification.reviewer_note)
        verification.reviewed_by = self.request.user
        verification.reviewed_at = timezone.now()
        from apps.accounts.services import delete_verification_id_image
        has_id_image = delete_verification_id_image(verification)
        fields_to_save = ["status", "reviewer_note", "reviewed_by", "reviewed_at"]
        if has_id_image:
            fields_to_save.append("student_id_image")
        verification.save(update_fields=fields_to_save)
        AdminLog.objects.create(actor=self.request.user, action=f"student_verification_{status_value}", target_type="StudentVerification", target_id=str(verification.pk), metadata={"user_id": user.pk})
        approved_label = "alumni" if verification.requested_affiliation == "alumni" else "student"
        if status_value == StudentVerification.Status.APPROVED:
            subject = "Your Tarmac verification was approved"
            body = f"Your {approved_label} verification was approved. Your account now has its verified {approved_label} status. Sign in to continue using Tarmac."
        else:
            subject = "Your Tarmac verification needs changes"
            reason = verification.reviewer_note or "The submitted details need correction. Contact Tarmac Support if you need help."
            body = f"Your verification was not approved. Administrator's reason: {reason}\n\nPlease correct the issue and submit a new verification request. Your student ID image has been deleted. If the note concerns account details you cannot edit, contact Tarmac Support before resubmitting."
        event_key = f"verification-reviewed:{verification.pk}:{status_value}"
        notify_user(
            user,
            subject,
            body,
            {"kind": "verification_review", "status": status_value, "reviewer_note": verification.reviewer_note, "verification_id": verification.pk},
            event_key,
            dispatch_email=True,
            force_email=True,
        )

    @action(detail=True, methods=["get"], url_path="student-id")
    def student_id(self, request, pk=None):
        verification = get_object_or_404(StudentVerification, pk=pk)
        if not verification.student_id_image:
            from rest_framework.exceptions import NotFound
            raise NotFound("No student ID image was submitted.")
        return optimized_image_response(verification.student_id_image)

class AdminReviewViewSet(viewsets.ModelViewSet):
    queryset = SellerReview.objects.select_related("seller", "reviewer", "order").all()
    serializer_class = AdminReviewSerializer
    permission_classes = [IsModeratorOrAbove]
    http_method_names = ["get", "patch", "delete", "head", "options"]
    filterset_fields = ["status", "rating", "category"]

    def perform_update(self, serializer):
        review = serializer.save()
        AdminLog.objects.create(actor=self.request.user, action=f"seller_review_{review.status}", target_type="SellerReview", target_id=str(review.pk), metadata={"seller_id": review.seller_id})

class AdminReviewReportViewSet(viewsets.ModelViewSet):
    queryset = ReviewReport.objects.select_related("review", "review__seller", "review__reviewer", "reporter").all()
    serializer_class = AdminReviewReportSerializer
    permission_classes = [IsModeratorOrAbove]
    http_method_names = ["get", "patch", "head", "options"]
    filterset_fields = ["status", "reason"]

    def perform_update(self, serializer):
        report = serializer.save()
        if report.status in (ReviewReport.Status.RESOLVED, ReviewReport.Status.DISMISSED):
            report.resolved_at = timezone.now()
            report.save(update_fields=("resolved_at",))
        AdminLog.objects.create(actor=self.request.user, action=f"review_report_{report.status}", target_type="ReviewReport", target_id=str(report.pk), metadata={"review_id": report.review_id, "review_status": report.review.status})

class AdminAccountViewSet(viewsets.ModelViewSet):
    queryset = User.objects.select_related("course", "anonymous_marketplace_identity").all().order_by("-date_joined")
    serializer_class = AdminAccountSerializer
    permission_classes = [IsAdminOrAbove]
    http_method_names = ["get", "patch", "head", "options"]
    search_fields = ["email", "username", "first_name", "last_name", "matric_number", "anonymous_marketplace_identity__handle"]

    def filter_queryset(self, queryset):
        queryset = super().filter_queryset(queryset)
        term = str(self.request.query_params.get("search", "")).strip()
        if term:
            try:
                public_id = UUID(term)
            except (ValueError, TypeError, AttributeError):
                return queryset
            return queryset | self.get_queryset().filter(public_id=public_id)
        return queryset

    def perform_update(self, serializer):
        account = serializer.save()
        if account.is_suspended or not account.is_active:
            for outstanding in OutstandingToken.objects.filter(user=account):
                BlacklistedToken.objects.get_or_create(token=outstanding)
        AdminLog.objects.create(actor=self.request.user, action="student_account_updated", target_type="User", target_id=str(account.pk), metadata={"is_active": account.is_active, "is_suspended": account.is_suspended})

    @action(detail=True, methods=["post"], url_path="enforcement")
    @transaction.atomic
    def enforcement(self, request, pk=None):
        account = User.objects.select_for_update().get(pk=pk)
        if account.role != User.Role.STUDENT or account.is_staff:
            raise ValidationError({"action": "Only student accounts can be suspended or banned here."})
        action_value = request.data.get("action")
        if action_value not in ("suspend", "ban", "restore"):
            raise ValidationError({"action": "Choose suspend, ban, or restore."})
        reason = str(request.data.get("reason", "")).strip()
        if action_value in ("suspend", "ban") and len(reason) < 5:
            raise ValidationError({"reason": "Add a reason of at least 5 characters."})

        if action_value == "suspend":
            account.is_active = True
            account.is_suspended = True
            subject = "Your Tarmac account has been suspended"
            body = f"Your Tarmac account has been temporarily suspended by an administrator. Reason: {reason}\n\nYou cannot sign in while the suspension is active. You may submit an appeal within 7 days using the button below."
            appeal_token = secrets.token_urlsafe(32)
            AccountActionToken.objects.filter(user=account, purpose=AccountActionToken.Purpose.SUSPENSION_APPEAL, used_at__isnull=True).update(used_at=timezone.now())
            AccountActionToken.objects.create(user=account, purpose=AccountActionToken.Purpose.SUSPENSION_APPEAL, token_hash=hashlib.sha256(appeal_token.encode()).hexdigest(), expires_at=timezone.now() + timedelta(days=7))
            email_url = f"{os.getenv('FRONTEND_ORIGIN', 'http://localhost:3000').rstrip('/')}/suspension-appeal?{urlencode({'token': appeal_token})}"
            audit_action = "student_account_suspended"
        elif action_value == "ban":
            account.is_active = False
            account.is_suspended = False
            subject = "Your Tarmac account has been banned"
            support_email = PlatformSettings.get_solo().support_email or os.getenv("SUPPORT_EMAIL", "")
            body = f"Your Tarmac account has been banned by an administrator. Reason: {reason}\n\nIf you believe this was a mistake, contact Tarmac Support{f' at {support_email}' if support_email else ''}."
            email_url = ""
            audit_action = "student_account_banned"
        else:
            account.is_active = True
            account.is_suspended = False
            account.deleted_at = None
            subject = "Your Tarmac account access has been restored"
            body = "An administrator has restored access to your Tarmac account. You can sign in again."
            email_url = f"{os.getenv('FRONTEND_ORIGIN', 'http://localhost:3000').rstrip('/')}/login"
            audit_action = "student_account_restored"

        account.save(update_fields=("is_active", "is_suspended", "deleted_at"))
        if not account.is_active or account.is_suspended:
            for outstanding in OutstandingToken.objects.filter(user=account):
                BlacklistedToken.objects.get_or_create(token=outstanding)
        AdminLog.objects.create(actor=request.user, action=audit_action, target_type="User", target_id=str(account.pk), metadata={"reason": reason})

        security_parameter = f"Submit an appeal: {email_url}" if email_url else body[:140]
        transaction.on_commit(lambda: notify_user(account, subject, body, {"kind": "account_enforcement", "notification_category": "account_security", "appeal_url": email_url or None, "template_parameter": security_parameter[:500]}, f"account-enforcement:{account.pk}:{action_value}:{timezone.now().timestamp()}"))
        return Response(self.get_serializer(account).data)

    @action(detail=True, methods=["post"], url_path="verification-badge")
    @transaction.atomic
    def verification_badge(self, request, pk=None):
        account = User.objects.select_for_update().get(pk=pk)
        if account.role != User.Role.STUDENT:
            raise ValidationError({"status": "Verification badges can only be managed for student accounts."})
        status_value = request.data.get("status")
        if status_value not in ("student", "alumni", "revoked"):
            raise ValidationError({"status": "Choose student, alumni, or revoked."})
        note = str(request.data.get("note", "")).strip()
        if status_value == "revoked" and len(note) < 3:
            raise ValidationError({"note": "Add a short reason for revoking the verification badge."})

        if status_value == "revoked":
            account.is_student_verified = False
            account.verified_affiliation = None
            account.student_status_override = None
            action_name = "student_verification_revoked"
            message = "Your student verification badge has been revoked by Tarmac administrators. Contact support if you believe this is an error."
        else:
            account.is_student_verified = True
            account.verified_affiliation = status_value
            account.student_status_override = status_value
            action_name = f"student_verification_badge_set_{status_value}"
            message = f"Your Tarmac verification badge was updated to {status_value}. Contact support if you have questions."
        account.save(update_fields=("is_student_verified", "verified_affiliation", "student_status_override"))
        AdminLog.objects.create(actor=request.user, action=action_name, target_type="User", target_id=str(account.pk), metadata={"status": status_value, "note": note})
        notify_user(account, "Tarmac verification badge updated", message, {"kind": "verification_badge_updated", "status": status_value}, f"verification-badge:{account.pk}:{status_value}:{timezone.now().timestamp()}")
        return Response(self.get_serializer(account).data)


class AdminProfileChangeRequestViewSet(viewsets.ModelViewSet):
    serializer_class = AdminProfileChangeRequestSerializer
    permission_classes = [IsAdminOrAbove]
    http_method_names = ["get", "post", "patch", "head", "options"]
    filterset_fields = ["status"]

    def create(self, request, *args, **kwargs):
        raise MethodNotAllowed("POST", detail="Profile change requests can only be created by students.")

    def get_queryset(self):
        return ProfileChangeRequest.objects.select_related("user", "user__anonymous_marketplace_identity", "reviewed_by").prefetch_related("messages__sender").all()

    search_fields = ["user__email", "user__username", "user__first_name", "user__last_name", "user__anonymous_marketplace_identity__handle"]

    def filter_queryset(self, queryset):
        queryset = super().filter_queryset(queryset)
        term = str(self.request.query_params.get("search", "")).strip()
        if term:
            try:
                public_id = UUID(term)
            except (ValueError, TypeError, AttributeError):
                return queryset
            return queryset | self.get_queryset().filter(user__public_id=public_id)
        return queryset

    @transaction.atomic
    def perform_update(self, serializer):
        change_request = ProfileChangeRequest.objects.select_for_update().select_related("user").get(pk=serializer.instance.pk)
        if change_request.status not in (ProfileChangeRequest.Status.PENDING, ProfileChangeRequest.Status.NEEDS_INFO):
            raise ValidationError({"status": "This request has already been reviewed."})
        next_status = serializer.validated_data.get("status")
        if next_status not in (ProfileChangeRequest.Status.APPROVED, ProfileChangeRequest.Status.REJECTED):
            raise ValidationError({"status": "Choose approve or reject after review."})
        if next_status == ProfileChangeRequest.Status.APPROVED:
            account = User.objects.select_for_update().get(pk=change_request.user_id)
            requested_changes = dict(change_request.requested_changes)
            requested_email = requested_changes.pop("email", None)
            requested_matric = requested_changes.pop("matric_number", None)
            if requested_email and User.objects.filter(email__iexact=requested_email).exclude(pk=account.pk).exists():
                raise ValidationError({"email": "Another account now uses the requested email address."})
            if requested_changes:
                profile_serializer = ProfileUpdateSerializer(account, data=requested_changes, partial=True)
                profile_serializer.is_valid(raise_exception=True)
                profile_serializer.save()
            if requested_email:
                account.email = requested_email
                account.email_verified = False
                account.save(update_fields=("email", "email_verified"))
                from apps.accounts.services import issue_account_action_token
                from apps.accounts.models import AccountActionToken
                AccountActionToken.objects.filter(user=account, purpose=AccountActionToken.Purpose.PASSWORD_RESET, used_at__isnull=True).update(used_at=timezone.now())
                issue_account_action_token(account, AccountActionToken.Purpose.EMAIL_VERIFY)
            if requested_matric:
                if User.objects.filter(matric_number__iexact=requested_matric).exclude(pk=account.pk).exists() or StudentVerification.objects.filter(submitted_matric_number__iexact=requested_matric).exclude(user=account).exists():
                    raise ValidationError({"matric_number": "Another account now uses this matriculation number."})
                account.matric_number = requested_matric
                account.is_student_verified = False
                account.save(update_fields=("matric_number", "is_student_verified"))
            if change_request.requested_profile_picture:
                account.profile_picture = change_request.requested_profile_picture
                account.save(update_fields=("profile_picture",))
        change_request.status = next_status
        change_request.reviewed_by = self.request.user
        change_request.reviewed_at = timezone.now()
        change_request.save(update_fields=("status", "reviewed_by", "reviewed_at", "updated_at"))
        change_request.user.refresh_from_db()
        AdminLog.objects.create(actor=self.request.user, action=f"profile_change_request_{next_status}", target_type="ProfileChangeRequest", target_id=str(change_request.pk), metadata={"user_id": change_request.user_id, "changed_fields": list(change_request.requested_changes)})
        review_result = "approved and applied to your account" if next_status == ProfileChangeRequest.Status.APPROVED else "rejected"
        notify_user(
            change_request.user,
            "Profile change request reviewed",
            f"Your profile change request #{change_request.pk} was {review_result}. Sign in and open Profile change requests on your Tarmac profile to view the decision and conversation.",
            {"kind": "profile_change_review", "request_id": change_request.pk, "status": next_status},
            f"profile-change-review:{change_request.pk}:{next_status}",
            dispatch_email=True,
            force_email=True,
        )
        serializer.instance = change_request

    @action(detail=True, methods=["post"])
    def question(self, request, pk=None):
        change_request = self.get_object()
        if change_request.status not in (ProfileChangeRequest.Status.PENDING, ProfileChangeRequest.Status.NEEDS_INFO):
            raise ValidationError("This request has already been reviewed.")
        body = str(request.data.get("body", "")).strip()
        if len(body) < 2 or len(body) > 3000:
            raise ValidationError({"body": "Write a question between 2 and 3000 characters."})
        ProfileChangeMessage.objects.create(request=change_request, sender=request.user, body=body)
        change_request.status = ProfileChangeRequest.Status.NEEDS_INFO
        change_request.save(update_fields=("status", "updated_at"))
        notify_user(
            change_request.user,
            "Admin has a question about your profile request",
            f"The school has a question about profile change request #{change_request.pk}:\n\n{body}\n\nSign in to Tarmac, open your profile, and reply in the Profile change requests section.",
            {"kind": "profile_change_question", "request_id": change_request.pk},
            f"profile-change-question:{change_request.pk}:{change_request.messages.count()}",
            dispatch_email=True,
            force_email=True,
        )
        return Response(self.get_serializer(change_request).data)

    @action(detail=True, methods=["get"], url_path="image")
    def image(self, request, pk=None):
        change_request = self.get_object()
        if not change_request.requested_profile_picture:
            from rest_framework.exceptions import NotFound
            raise NotFound("No profile picture was included in this request.")
        return optimized_image_response(change_request.requested_profile_picture)

class AdminConversationViewSet(viewsets.GenericViewSet):
    serializer_class = AdminConversationSerializer
    permission_classes = [IsAdminOrAbove]
    http_method_names = ["get", "patch", "post", "head", "options"]
    def get_queryset(self):
        latest_messages = Message.objects.select_related("sender", "conversation").prefetch_related("attachments", "conversation__participant_snapshots").order_by("-created_at")[:50]
        return Conversation.objects.annotate(unread_count=Count("messages", filter=models.Q(messages__sender__is_staff=False, messages__read_at__isnull=True))).prefetch_related("participants", "participant_snapshots", Prefetch("messages", queryset=latest_messages, to_attr="recent_messages")).order_by("-updated_at")
    def list(self, request):
        queryset = self.get_queryset()
        page = self.paginate_queryset(queryset)
        return self.get_paginated_response(self.get_serializer(page, many=True).data) if page is not None else Response(self.get_serializer(queryset, many=True).data)
    def retrieve(self, request, pk=None): return Response(self.get_serializer(self.get_object()).data)
    @action(detail=True, methods=["get"], url_path="messages")
    def messages(self, request, pk=None):
        """Return paginated conversation history for admin review."""
        conversation = self.get_object()
        queryset = conversation.messages.select_related("sender", "conversation").prefetch_related(
            "attachments", "conversation__participant_snapshots"
        ).order_by("-created_at", "-pk")
        page = self.paginate_queryset(queryset)
        serializer = AdminConversationMessageSerializer(
            page if page is not None else queryset,
            many=True,
            context=self.get_serializer_context(),
        )
        return self.get_paginated_response(serializer.data) if page is not None else Response(serializer.data)
    def partial_update(self, request, pk=None):
        conversation = self.get_object()
        value = request.data.get("status")
        if value not in Conversation.Status.values: raise ValidationError({"status": "Choose open or resolved."})
        was_resolved = conversation.status == Conversation.Status.RESOLVED
        conversation.status = value
        if value == Conversation.Status.RESOLVED and not was_resolved:
            conversation.completed_at = timezone.now()
            for student in conversation.participants.filter(is_staff=False):
                notify_user(student, "Tarmac conversation marked complete", "An administrator marked this Tarmac conversation as complete. You can reply to reopen it.", {"kind": "conversation_completed", "conversation_id": conversation.pk, "context_type": conversation.context_type}, f"admin-conversation-completed:{conversation.pk}:{conversation.completed_at.timestamp()}:{student.pk}")
        elif value == Conversation.Status.OPEN:
            conversation.completed_at = None
            conversation.completed_by.clear()
        conversation.save(update_fields=("status", "completed_at", "updated_at"))
        AdminLog.objects.create(actor=request.user, action=f"conversation_{value}", target_type="Conversation", target_id=str(conversation.pk), metadata={})
        return Response(self.get_serializer(conversation).data)
    @action(detail=True, methods=["post"], url_path="mark-read")
    def mark_read(self, request, pk=None):
        conversation = self.get_object()
        conversation.messages.filter(read_at__isnull=True).exclude(sender__is_staff=True).update(read_at=timezone.now())
        return Response({"conversation": conversation.pk, "read": True})
    @action(detail=True, methods=["post"])
    def reply(self, request, pk=None):
        conversation = self.get_object()
        body = str(request.data.get("body", "")).strip()
        uploads = request.FILES.getlist("attachment_files")
        serializer = MessageSerializer(data={"conversation": conversation.pk, "body": body, "attachment_files": uploads}, context={"request": request})
        serializer.is_valid(raise_exception=True)
        message = serializer.save(sender=request.user)
        if not conversation.is_official:
            conversation.is_official = True
        conversation.status = Conversation.Status.OPEN
        conversation.completed_at = None
        conversation.completed_by.clear()
        conversation.save(update_fields=("is_official", "status", "completed_at", "updated_at"))
        for student in conversation.participants.filter(is_staff=False):
            category = {Conversation.ContextType.OFFICIAL_SUPPORT: "support", Conversation.ContextType.OFFICIAL_PROMO: "promotional", Conversation.ContextType.OFFICIAL_ALERT: "lost_found"}.get(conversation.context_type, "official")
            whatsapp_kind = {"support": "support_reply", "promotional": "promotion", "lost_found": "lost_found_match", "official": "official_message"}[category]
            notify_user(student, "New message from Tarmac administration", body[:240], {"kind":"admin_conversation_reply", "whatsapp_template_kind": whatsapp_kind, "template_parameter": body[:140], "notification_category": category, "conversation_id":conversation.pk}, f"admin-conversation-reply:{message.pk}:{student.pk}")
        AdminLog.objects.create(actor=request.user, action="conversation_admin_reply", target_type="Conversation", target_id=str(conversation.pk), metadata={"message_id":message.pk})
        conversation = self.get_queryset().get(pk=conversation.pk)
        return Response(AdminConversationSerializer(conversation, context={"request": request}).data, status=status.HTTP_201_CREATED)

class SubscriptionPlanViewSet(viewsets.ModelViewSet):
    serializer_class = SubscriptionPlanSerializer
    def get_queryset(self):
        queryset = SubscriptionPlan.objects.all()
        return queryset if self.request.user.is_staff else queryset.filter(is_active=True)
    def get_permissions(self): return [permissions.AllowAny()] if self.action in ("list", "retrieve") else [IsAdminOrAbove()]
    def perform_create(self, serializer):
        plan = serializer.save(); AdminLog.objects.create(actor=self.request.user, action="subscription_plan_created", target_type="SubscriptionPlan", target_id=str(plan.pk), metadata={"name": plan.name, "price_kobo": plan.price_kobo, "duration_days": plan.duration_days, "allowed_pages": plan.allowed_pages, "allowed_placements": plan.allowed_placements, "is_active": plan.is_active})
    def perform_update(self, serializer):
        instance = serializer.instance
        before = {"name": instance.name, "price_kobo": instance.price_kobo, "duration_days": instance.duration_days, "allowed_pages": instance.allowed_pages, "allowed_placements": instance.allowed_placements, "is_active": instance.is_active}
        plan = serializer.save()
        after = {"name": plan.name, "price_kobo": plan.price_kobo, "duration_days": plan.duration_days, "allowed_pages": plan.allowed_pages, "allowed_placements": plan.allowed_placements, "is_active": plan.is_active}
        AdminLog.objects.create(actor=self.request.user, action="subscription_plan_updated", target_type="SubscriptionPlan", target_id=str(plan.pk), metadata={"before": before, "after": after, "note": "Changes apply to new campaigns. Existing campaigns retain their submitted package and payment terms."})

class StudentSubscriptionViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = StudentSubscriptionSerializer
    def get_permissions(self): return [IsAdminOrAbove()] if self.request.user.is_staff else [IsVerifiedStudent()]
    def get_queryset(self):
        queryset = StudentSubscription.objects.select_related("user", "plan")
        return queryset if self.request.user.is_staff else queryset.filter(user=self.request.user)

@api_view(["GET"])
@drf_permission_classes([IsAdminOrAbove])
def dashboard(request):
    cached = cache.get("tarmac.admin.dashboard.v1")
    if cached is not None:
        return Response(cached)
    today = timezone.localdate()
    month_start = today.replace(day=1)
    week_start = today - timedelta(days=6)
    payments = Payment.objects.filter(status=Payment.Status.SUCCESS)
    payload = {
        "students": {"total": User.objects.filter(role=User.Role.STUDENT).count(), "verified": User.objects.filter(role=User.Role.STUDENT, is_student_verified=True).count(), "unverified": User.objects.filter(role=User.Role.STUDENT, is_student_verified=False).count(), "suspended": User.objects.filter(is_suspended=True).count(), "registered_today": User.objects.filter(date_joined__date=today).count(), "registered_last_7_days": User.objects.filter(date_joined__date__gte=week_start).count(), "registered_this_month": User.objects.filter(date_joined__date__gte=month_start).count()},
        "marketplace": {"active": Product.objects.filter(status=Product.Status.ACTIVE).count(), "pending_review": Product.objects.filter(status=Product.Status.PENDING_REVIEW).count(), "expired": Product.objects.filter(status=Product.Status.EXPIRED).count(), "reported": UserReport.objects.filter(product__isnull=False, is_resolved=False).count(), "completed_sales": Product.objects.filter(status=Product.Status.SOLD).count(), "top_categories": list(Product.objects.filter(status=Product.Status.ACTIVE).values("category__name").annotate(listings=Count("id")).order_by("-listings")[:10]), "active_sellers": list(User.objects.filter(products__status=Product.Status.ACTIVE).values("id", "username").annotate(listings=Count("products")).order_by("-listings")[:10])},
        "payments": {"successful_count": payments.count(), "successful_revenue_kobo": payments.aggregate(total=Sum("amount_kobo"))["total"] or 0, "this_month_revenue_kobo": payments.filter(paid_at__date__gte=month_start).aggregate(total=Sum("amount_kobo"))["total"] or 0, "failed_count": Payment.objects.filter(status=Payment.Status.FAILED).count()},
        "lost_found": {"lost_open": LostFoundReport.objects.filter(report_type=LostFoundReport.Type.LOST, is_active=True).count(), "found_open": LostFoundReport.objects.filter(report_type=LostFoundReport.Type.FOUND, is_active=True).count(), "potential_matches": ItemMatch.objects.filter(is_dismissed=False).count(), "frequent_lost_items": list(LostFoundReport.objects.filter(report_type=LostFoundReport.Type.LOST).values("title").annotate(reports=Count("id")).order_by("-reports")[:10])},
        "ads": {"active": AdCampaign.objects.filter(status=AdCampaign.Status.ACTIVE, ends_at__gt=timezone.now()).count(), "pending_review": AdCampaign.objects.filter(status=AdCampaign.Status.PENDING_REVIEW).count()},
        "reports_unresolved": UserReport.objects.filter(is_resolved=False).count(),
        "workflow": {
            "pending_verifications": StudentVerification.objects.filter(status=StudentVerification.Status.PENDING).count(),
            "pending_profile_changes": ProfileChangeRequest.objects.filter(status=ProfileChangeRequest.Status.PENDING).count(),
            "open_support_tickets": SupportTicket.objects.filter(status__in=(SupportTicket.Status.OPEN, SupportTicket.Status.IN_PROGRESS)).count(),
            "pending_lost_found_claims": ItemClaim.objects.filter(status=ItemClaim.Status.PENDING).count(),
            "pending_lost_found_matches": ItemMatch.objects.filter(is_reviewed=False, is_dismissed=False).count(),
            "pending_reviews": SellerReview.objects.filter(status=SellerReview.Status.PENDING).count(),
            "open_review_reports": ReviewReport.objects.filter(status__in=(ReviewReport.Status.OPEN, ReviewReport.Status.REVIEWING)).count(),
            "pending_job_listings": JobListing.objects.filter(status=JobListing.Status.PENDING_REVIEW).count(),
            "pending_accommodation_listings": AccommodationListing.objects.filter(status=AccommodationListing.Status.PENDING_REVIEW).count(),
            "pending_hostel_agent_requests": HostelAgentRequest.objects.filter(status=HostelAgentRequest.Status.PENDING).count(),
            "open_job_reports": JobReport.objects.filter(status__in=(JobReport.Status.OPEN, JobReport.Status.REVIEWING)).count(),
            "waiting_advertisers": AdWaitlistEntry.objects.filter(status=AdWaitlistEntry.Status.WAITING).count(),
        },
    }
    cache.set("tarmac.admin.dashboard.v1", payload, 15)
    return Response(payload)


@api_view(["GET"])
@drf_permission_classes([IsModeratorOrAbove])
def queue_counts(request):
    """Small navigation badge payload; avoids fetching full admin dashboard data."""
    cached = cache.get(f"tarmac.admin.queue_counts.v2.{request.user.pk}")
    if cached is not None:
        return Response(cached)
    payload = {
        "verification": StudentVerification.objects.filter(status=StudentVerification.Status.PENDING).count(),
        "profile_changes": ProfileChangeRequest.objects.filter(status=ProfileChangeRequest.Status.PENDING).count(),
        "marketplace": Product.objects.filter(status=Product.Status.PENDING_REVIEW).count(),
        "jobs": JobListing.objects.filter(status=JobListing.Status.PENDING_REVIEW).count() + JobReport.objects.filter(status__in=(JobReport.Status.OPEN, JobReport.Status.REVIEWING)).count(),
        "accommodation": AccommodationListing.objects.filter(status=AccommodationListing.Status.PENDING_REVIEW).count() + HostelAgentRequest.objects.filter(status=HostelAgentRequest.Status.PENDING).count(),
        "lost_found": LostFoundReport.objects.filter(status=LostFoundReport.Status.PENDING_REVIEW).count() + ItemClaim.objects.filter(status=ItemClaim.Status.PENDING).count() + ItemMatch.objects.filter(is_reviewed=False, is_dismissed=False).count(),
        "reports": UserReport.objects.filter(is_resolved=False).count(),
        "reviews": SellerReview.objects.filter(status=SellerReview.Status.PENDING).count() + ReviewReport.objects.filter(status__in=(ReviewReport.Status.OPEN, ReviewReport.Status.REVIEWING)).count(),
        "ads": AdCampaign.objects.filter(status=AdCampaign.Status.PENDING_REVIEW).count() + AdWaitlistEntry.objects.filter(status=AdWaitlistEntry.Status.WAITING).count(),
        "support": SupportTicket.objects.filter(status__in=(SupportTicket.Status.OPEN, SupportTicket.Status.IN_PROGRESS)).count(),
    }
    if not request.user.is_superuser:
        assigned = set(request.user.staff_notification_sections or [])
        if "profile_changes" in assigned:
            payload["profile_changes"] = ProfileChangeRequest.objects.filter(status=ProfileChangeRequest.Status.PENDING).count()
        payload = {section: count if section in assigned else 0 for section, count in payload.items()}
    cache.set(f"tarmac.admin.queue_counts.v2.{request.user.pk}", payload, 5)
    return Response(payload)

@api_view(["GET"])
@drf_permission_classes([IsAdminOrAbove])
def analytics(request):
    try:
        days = int(request.query_params.get("days", 30))
    except (TypeError, ValueError):
        raise ValidationError({"days": "Choose a period of 7, 30, or 90 days."})
    if days not in (7, 30, 90):
        raise ValidationError({"days": "Choose a period of 7, 30, or 90 days."})
    cache_key = f"tarmac.admin.analytics.v1.{days}"
    cached = cache.get(cache_key)
    if cached is not None:
        return Response(cached)
    start = timezone.localdate() - timedelta(days=days - 1)
    registrations = {row["day"].isoformat(): row["total"] for row in User.objects.filter(date_joined__date__gte=start).annotate(day=TruncDate("date_joined")).values("day").annotate(total=Count("id"))}
    payments = {row["day"].isoformat(): row for row in Payment.objects.filter(status=Payment.Status.SUCCESS, paid_at__date__gte=start).annotate(day=TruncDate("paid_at")).values("day").annotate(count=Count("id"), revenue_kobo=Sum("amount_kobo"))}
    lost_found = {row["day"].isoformat(): row["total"] for row in LostFoundReport.objects.filter(created_at__date__gte=start).annotate(day=TruncDate("created_at")).values("day").annotate(total=Count("id"))}
    series = []
    for offset in range(days):
        day = (start + timedelta(days=offset)).isoformat()
        payment = payments.get(day, {})
        series.append({"date": day, "registrations": registrations.get(day, 0), "payments": payment.get("count", 0), "revenue_kobo": payment.get("revenue_kobo") or 0, "lost_found_reports": lost_found.get(day, 0)})
    payload = {"days": days, "series": series, "totals": {"registrations": sum(row["registrations"] for row in series), "payments": sum(row["payments"] for row in series), "revenue_kobo": sum(row["revenue_kobo"] for row in series), "lost_found_reports": sum(row["lost_found_reports"] for row in series)}}
    cache.set(cache_key, payload, 60)
    return Response(payload)

@api_view(["POST"])
@drf_permission_classes([IsAdminOrAbove])
@transaction.atomic
def send_admin_message(request):
    email = str(request.data.get("email", "")).strip().lower()
    subject = str(request.data.get("subject", "")).strip()
    body = str(request.data.get("body", "")).strip()
    message_type = str(request.data.get("message_type", "official")).strip()
    message_types = {
        "official": (Conversation.ContextType.OFFICIAL, "official"),
        "support": (Conversation.ContextType.OFFICIAL_SUPPORT, "support"),
        "promotion": (Conversation.ContextType.OFFICIAL_PROMO, "promotional"),
    }
    if message_type not in message_types:
        raise ValidationError({"message_type": "Choose official, support, or promotion."})
    context_type, notification_group = message_types[message_type]
    uploads = request.FILES.getlist("attachment_files")
    if not email or not subject or (not body and not uploads):
        raise ValidationError({"detail": "Student email and title are required, with a message or attachment."})
    if len(subject) > 180 or len(body) > 5000:
        raise ValidationError({"detail": "Message title must be 180 characters or fewer and message 5,000 or fewer."})
    user = User.objects.filter(email__iexact=email, is_active=True, is_suspended=False).first()
    if not is_student_feature_eligible(user):
        raise ValidationError({"email": "No active eligible student account was found with this email."})
    conversation = Conversation.objects.annotate(participant_count=Count("participants", distinct=True)).filter(
        context_type=context_type, is_official=True, participant_count=2, participants=request.user
    ).filter(participants=user).first()
    if conversation is None:
        conversation = Conversation.objects.create(context_type=context_type, context_title=subject[:180], is_official=True)
        conversation.participants.add(request.user, user)
    serializer = MessageSerializer(data={"conversation": conversation.pk, "body": f"{subject}\n\n{body}".strip(), "attachment_files": uploads}, context={"request": request})
    serializer.is_valid(raise_exception=True)
    message = serializer.save(sender=request.user)
    conversation.is_official = True
    conversation.context_title = subject[:180]
    conversation.save(update_fields=("is_official", "context_title", "updated_at"))
    whatsapp_kind = {"support": "support_reply", "promotional": "promotion", "official": "official_message"}[notification_group]
    notify_user(user, "Official message from Tarmac", body[:240] or subject, {"kind": "admin_conversation_reply", "whatsapp_template_kind": whatsapp_kind, "template_parameter": body[:140] or subject[:140], "notification_category": notification_group, "conversation_id": conversation.pk, "is_official": True}, f"admin-message:{message.pk}:{user.pk}")
    AdminLog.objects.create(actor=request.user, action="student_message_sent", target_type="User", target_id=str(user.pk), metadata={"email": user.email, "message_type": message_type})
    return Response({"sent": True, "student": user.email, "conversation_id": conversation.pk}, status=status.HTTP_201_CREATED)

from rest_framework.permissions import AllowAny

@api_view(["GET"])
@drf_permission_classes([AllowAny])
def system_status(request):
    config = PlatformSettings.get_solo()
    return Response({
        "site": {
            "name": config.site_name,
            "tagline": config.tagline,
            "logo_url": request.build_absolute_uri("/api/system/logo/") if config.site_logo else "",
            "support_email": config.support_email,
            "contact_email": config.contact_email,
        },
        "campuses": config.campuses or [],
        "campus_areas": config.campus_areas or {},
        "maintenance_mode": config.maintenance_mode,
        "maintenance_message": config.maintenance_message if config.maintenance_mode else "",
        "features": {
            "marketplace": config.marketplace_enabled,
            "lost_found": config.lost_found_enabled,
            "jobs": config.jobs_enabled,
            "accommodation": config.accommodation_enabled,
            "external_job_submissions": config.external_job_submissions_enabled,
            "messaging": config.messaging_enabled,
            "ads": config.ads_enabled,
            "ad_fees_enabled": config.ad_fee_enabled,
            "paystack_enabled": config.paystack_enabled,
            "ad_slide_duration_seconds": config.ad_slide_duration_seconds,
            "homepage_ads": config.homepage_ads_enabled,
            "marketplace_ads": config.marketplace_ads_enabled,
            "homepage_lost_found": config.homepage_lost_found_enabled,
            "featured_products": config.featured_products_enabled,
            "reviews": config.reviews_enabled,
            "announcements": config.announcements_enabled,
            "student_verification": config.student_verification_enabled,
            "email_verification": config.email_verification_required,
            "registration": config.registration_enabled,
        },
        "listing": {
            "approval_required": config.listing_approval_required,
            "max_images": config.max_product_images,
            "require_student_id": config.require_student_id,
        },
        "fees": {
            "listing_fee_required": config.listing_fee_enabled and config.paystack_enabled,
            "listing_fee_kobo": config.listing_fee_kobo if config.listing_fee_enabled and config.paystack_enabled else 0,
            "ad_fee_required": config.ad_fee_enabled and config.paystack_enabled,
            "ad_fee_kobo": config.ad_fee_kobo if config.ad_fee_enabled and config.paystack_enabled else 0,
            "payments_enabled": config.paystack_enabled,
        },
        "sponsorship": {
            "enabled": config.sponsored_products_enabled,
            "fee_required": config.sponsored_product_fee_enabled and config.paystack_enabled,
            "fee_kobo": config.sponsored_product_fee_kobo if config.sponsored_product_fee_enabled and config.paystack_enabled else 0,
            "duration_days": config.sponsored_product_duration_days,
        },
        "policies": {
            "terms": config.terms_text,
            "privacy": config.privacy_text,
            "community_guidelines": config.community_guidelines_text,
        },
    })


@api_view(["GET"])
@drf_permission_classes([AllowAny])
def system_logo(request):
    config = PlatformSettings.get_solo()
    if not config.site_logo:
        from rest_framework.exceptions import NotFound
        raise NotFound("The site logo has not been set.")
    return optimized_image_response(config.site_logo)

class SupportTicketViewSet(ActionRateLimitMixin, viewsets.ModelViewSet):
    action_throttle_scopes = {
        "create": "support_ticket_create",
        "reply": "support_reply",
    }
    serializer_class = SupportTicketSerializer
    permission_classes = [IsAdminOrAbove | IsVerifiedStudent]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        queryset = SupportTicket.objects.select_related("user").prefetch_related("messages__sender", "messages__attachments")
        return queryset if self.request.user.is_staff else queryset.filter(user=self.request.user)

    def get_permissions(self):
        if self.request.user and self.request.user.is_staff:
            return [IsAdminOrAbove()]
        return super().get_permissions()

    def perform_create(self, serializer):
        ticket = serializer.save(user=self.request.user)
        from apps.notifications.staff import notify_staff_section
        try:
            notify_staff_section("support", "New student support request", ticket.subject, {"kind": "support_ticket", "ticket_id": ticket.pk}, f"support-ticket:{ticket.pk}")
        except Exception:
            logger.exception("Could not notify staff about support ticket %s", ticket.pk)

    def perform_update(self, serializer):
        previous_status = serializer.instance.status
        ticket = serializer.save()
        AdminLog.objects.create(actor=self.request.user, action="support_ticket_updated", target_type="SupportTicket", target_id=str(ticket.pk), metadata={"status": ticket.status, "priority": ticket.priority})
        if ticket.status == SupportTicket.Status.RESOLVED and previous_status != SupportTicket.Status.RESOLVED:
            from apps.notifications.services import notify_user
            notify_user(ticket.user, "Tarmac support request completed", f'Your support request "{ticket.subject}" was marked completed. You can reopen it from your support page if you still need help.', {"kind": "support_completed", "ticket_id": ticket.pk}, f"support-completed:{ticket.pk}:{ticket.updated_at.timestamp()}")

    @action(detail=True, methods=("post",))
    def reopen(self, request, pk=None):
        ticket = self.get_object()
        if ticket.status != SupportTicket.Status.RESOLVED:
            raise ValidationError({"status": "Only resolved tickets can be reopened."})
        ticket.status = SupportTicket.Status.OPEN
        ticket.save(update_fields=("status", "updated_at"))
        AdminLog.objects.create(actor=request.user, action="support_ticket_reopened", target_type="SupportTicket", target_id=str(ticket.pk), metadata={})
        notify_user(ticket.user, "Tarmac support request reopened", f'Your support request "{ticket.subject}" has been reopened.', {"kind":"support_ticket_reopened", "ticket_id":ticket.pk}, f"support-ticket-reopen:{ticket.pk}:{ticket.updated_at.timestamp()}:{ticket.user_id}")
        if not request.user.is_staff:
            from apps.notifications.staff import notify_staff_section
            notify_staff_section("support", "Student reopened a support request", ticket.subject, {"kind":"support_ticket_reopened", "ticket_id":ticket.pk}, f"support-ticket-reopen:{ticket.pk}:{ticket.updated_at.timestamp()}")
        return Response(SupportTicketSerializer(ticket, context={"request": request}).data)

    @action(detail=True, methods=("post",))
    def complete(self, request, pk=None):
        ticket = self.get_object()
        if ticket.status == SupportTicket.Status.RESOLVED:
            return Response(SupportTicketSerializer(ticket, context={"request": request}).data)
        ticket.status = SupportTicket.Status.RESOLVED
        ticket.save(update_fields=("status", "updated_at"))
        AdminLog.objects.create(actor=request.user, action="support_ticket_completed", target_type="SupportTicket", target_id=str(ticket.pk), metadata={"student_id": ticket.user_id})
        from apps.notifications.services import notify_user
        if request.user.is_staff:
            notify_user(ticket.user, "Tarmac support request completed", f'Your support request "{ticket.subject}" was marked completed by Tarmac Support.', {"kind": "support_completed", "ticket_id": ticket.pk}, f"support-completed:{ticket.pk}:{ticket.updated_at.timestamp()}:{ticket.user_id}")
        else:
            notify_user(ticket.user, "Support request marked complete", f'You marked "{ticket.subject}" as completed. Reopen it from Support if you still need help.', {"kind": "support_completed", "ticket_id": ticket.pk}, f"support-completed:{ticket.pk}:{ticket.updated_at.timestamp()}:{ticket.user_id}")
            from apps.notifications.staff import notify_staff_section
            notify_staff_section("support", "Student marked a support request complete", ticket.subject, {"kind": "support_completed", "ticket_id": ticket.pk}, f"support-completed:{ticket.pk}:{ticket.updated_at.timestamp()}")
        return Response(SupportTicketSerializer(ticket, context={"request": request}).data)

    @action(detail=True, methods=("post",))
    def reply(self, request, pk=None):
        ticket = self.get_object()
        body = str(request.data.get("body", "")).strip()
        uploads = request.data.getlist("attachment_files") if hasattr(request.data, "getlist") else []
        if not body or len(body) > 5000:
            raise ValidationError({"body": "Enter a reply of 1 to 5,000 characters."})
        if len(uploads) > 5:
            raise ValidationError({"attachment_files": "Attach at most five files."})
        for upload in uploads:
            if upload.content_type not in ("image/jpeg", "image/png", "image/webp", "application/pdf"):
                raise ValidationError({"attachment_files": "Use JPG, PNG, WEBP or PDF files."})
            limit = 5 if upload.content_type == "application/pdf" else 25
            if upload.size > limit * 1024 * 1024:
                raise ValidationError({"attachment_files": "Images must be 25 MB and PDFs 5 MB or smaller."})
            if upload.content_type == "application/pdf":
                from config.upload_validation import validate_pdf_upload
                try:
                    validate_pdf_upload(upload)
                except ValueError as error:
                    raise ValidationError({"attachment_files": str(error)}) from error
        if ticket.status == SupportTicket.Status.RESOLVED and not request.user.is_staff:
            raise ValidationError({"status": "This ticket is resolved. Ask an administrator to reopen it."})
        from django.utils.text import get_valid_filename
        with transaction.atomic():
            message = SupportMessage.objects.create(ticket=ticket, sender=request.user, body=body)
            for upload in uploads:
                original_name = get_valid_filename(upload.name)[:255]
                content_type = upload.content_type
                if content_type.startswith("image/"):
                    from config.storage import optimize_uploaded_image
                    try:
                        stored_file = optimize_uploaded_image(upload, max_size=(1440, 1440), quality=78)
                    except ValueError as error:
                        raise ValidationError({"attachment_files": "Upload a valid image file."}) from error
                    stored_file.name = f"{original_name.rsplit('.', 1)[0]}.webp"
                    content_type = "image/webp"
                else:
                    stored_file = upload
                SupportAttachment.objects.create(message=message, file=stored_file, original_name=original_name, content_type=content_type)
            if ticket.status == SupportTicket.Status.OPEN and request.user.is_staff:
                ticket.status = SupportTicket.Status.IN_PROGRESS
                ticket.save(update_fields=("status", "updated_at"))
        from apps.notifications.services import notify_user
        if request.user.is_staff:
            notify_user(ticket.user, f"Support replied: {ticket.subject}", body[:500], {"kind": "support_reply", "ticket_id": ticket.pk}, f"support-reply:{message.pk}:{ticket.user_id}")
        else:
            from apps.notifications.staff import notify_staff_section
            notify_staff_section("support", f"Student replied: {ticket.subject}", body[:500], {"kind": "support_reply", "ticket_id": ticket.pk}, f"support-reply:{message.pk}")
        ticket.refresh_from_db()
        return Response(SupportTicketSerializer(ticket, context={"request": request}).data)


@api_view(["GET"])
@drf_permission_classes([permissions.IsAuthenticated])
def support_attachment_file(request, attachment_id):
    attachment = get_object_or_404(SupportAttachment.objects.select_related("message__ticket"), pk=attachment_id)
    is_admin = IsAdminOrAbove().has_permission(request, None)
    if not is_admin and attachment.message.ticket.user_id != request.user.pk:
        from rest_framework.exceptions import PermissionDenied
        raise PermissionDenied("You cannot view attachments for another student's support ticket.")
    is_image = attachment.content_type == "image/webp"
    response = FileResponse(
        attachment.file.open("rb"),
        content_type="image/webp" if is_image else "application/pdf",
        as_attachment=not is_image,
        filename=attachment.original_name if not is_image else None,
    )
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    response["Vary"] = "Cookie"
    return response

class IsSuperUser(permissions.BasePermission):
    message = "Only a superuser can change platform-wide settings."
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_superuser)


class AnnouncementPublisherAccessViewSet(viewsets.GenericViewSet):
    serializer_class = AnnouncementPublisherSerializer
    permission_classes = [IsSuperUser]
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        return AnnouncementPublisher.objects.select_related("user", "granted_by").all()

    def list(self, request):
        return Response(self.get_serializer(self.get_queryset(), many=True).data)

    @transaction.atomic
    def create(self, request):
        email = str(request.data.get("email", "")).strip()
        if not email:
            raise ValidationError({"email": "Enter the email address of an existing account with a verified email."})
        account = User.objects.select_for_update().filter(email__iexact=email).first()
        if account is None:
            raise ValidationError({"email": "No account exists with that email. Ask the representative to register first."})
        if account.is_staff or account.is_superuser:
            raise ValidationError({"email": "Choose a non-staff account. Staff already manage announcements through their staff role."})
        if not account.email_verified or not account.is_active or account.is_suspended or account.deleted_at:
            raise ValidationError({"email": "The account must be active, not suspended or deleted, and have a verified email."})

        access = AnnouncementPublisher.objects.select_for_update().filter(user=account).first()
        if access and access.is_active:
            return Response(self.get_serializer(access).data)
        created = access is None
        if created:
            access = AnnouncementPublisher.objects.create(user=account, granted_by=request.user)
        else:
            access.granted_by = request.user
            access.is_active = True
            access.revoked_at = None
            access.save(update_fields=("granted_by", "is_active", "revoked_at", "updated_at"))
        AdminLog.objects.create(actor=request.user, action="announcement_publisher_access_granted", target_type="User", target_id=str(account.pk), metadata={"email": account.email})
        notify_user(account, "Announcement publishing access granted", "You can now post school announcements for verified Tarmac students. Use the Announcement Publisher link in your Tarmac dashboard. This permission does not grant access to the admin panel.", {"kind": "announcement_publisher_access", "notification_category": "official", "url": "/announcement-publisher", "link": "/announcement-publisher"}, f"announcement-publisher-granted:{account.pk}:{access.updated_at.timestamp()}", dispatch_email=True)
        return Response(self.get_serializer(access).data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    @action(detail=True, methods=("post",), url_path="revoke")
    @transaction.atomic
    def revoke(self, request, pk=None):
        access = get_object_or_404(self.get_queryset().select_for_update(), pk=pk)
        if access.is_active:
            access.is_active = False
            access.revoked_at = timezone.now()
            access.save(update_fields=("is_active", "revoked_at", "updated_at"))
            AdminLog.objects.create(actor=request.user, action="announcement_publisher_access_revoked", target_type="User", target_id=str(access.user_id), metadata={"email": access.user.email})
            notify_user(access.user, "Announcement publishing access removed", "Your permission to publish announcements on Tarmac has been removed. Your normal account is unchanged.", {"kind": "announcement_publisher_access", "notification_category": "official"}, f"announcement-publisher-revoked:{access.user_id}:{access.updated_at.timestamp()}", dispatch_email=True)
        return Response(self.get_serializer(access).data)


class AdminAdministratorViewSet(viewsets.GenericViewSet):
    serializer_class = AdminAdministratorSerializer
    permission_classes = [IsSuperUser]
    http_method_names = ["get", "post", "head", "options"]

    STAFF_ROLES = {
        "moderator": User.Role.MODERATOR,
        "admin": User.Role.ADMIN,
        "super_admin": User.Role.SUPER_ADMIN,
    }

    def get_queryset(self):
        return User.objects.filter(is_staff=True).order_by("-is_superuser", "email")

    def list(self, request):
        return Response(self.get_serializer(self.get_queryset(), many=True).data)

    @transaction.atomic
    def create(self, request):
        email = str(request.data.get("email", "")).strip()
        if not email:
            raise ValidationError({"email": "Enter the email address of an existing, verified account."})
        account = User.objects.select_for_update().filter(email__iexact=email).first()
        if not account:
            raise ValidationError({"email": "No account exists with that email. The person must register and verify their email first."})
        if account.is_staff or account.is_superuser:
            raise ValidationError({"email": "This account already has administrator access."})
        if not account.is_active or account.is_suspended:
            raise ValidationError({"email": "Inactive or suspended accounts cannot become administrators."})
        if not account.email_verified:
            raise ValidationError({"email": "The account must verify its email before administrator access can be granted."})

        role = request.data.get("role", User.Role.ADMIN)
        if role not in self.STAFF_ROLES:
            raise ValidationError({"role": "Choose moderator, admin, or super_admin."})
        from apps.notifications.staff import valid_staff_sections
        sections = request.data.get("staff_notification_sections", [])
        if not valid_staff_sections(sections):
            raise ValidationError({"staff_notification_sections": "Choose valid notification sections."})

        account.is_staff = True
        account.is_superuser = role == User.Role.SUPER_ADMIN
        account.role = self.STAFF_ROLES[role]
        account.staff_notification_sections = [] if account.is_superuser else sections
        account.save(update_fields=("is_staff", "is_superuser", "role", "staff_notification_sections"))
        AdminLog.objects.create(actor=request.user, action="administrator_access_granted", target_type="User", target_id=str(account.pk), metadata={"role": role, "staff_notification_sections": account.staff_notification_sections})
        event_key = f"administrator-access:{account.pk}:granted:{request.user.pk}:{timezone.now().timestamp()}"
        transaction.on_commit(lambda: notify_user(account, "Tarmac staff access granted", f"A Tarmac superuser granted {role.replace('_', ' ')} access to your account. Contact Tarmac Support immediately if you did not expect this change.", {"kind": "account_enforcement", "notification_category": "account_security", "staff_role": role}, event_key))
        return Response(self.get_serializer(account).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="role")
    @transaction.atomic
    def change_role(self, request, pk=None):
        account = get_object_or_404(User.objects.select_for_update(), pk=pk)
        role = request.data.get("role")
        if role not in self.STAFF_ROLES:
            raise ValidationError({"role": "Choose moderator, admin, or super_admin."})
        if not account.is_staff or not account.is_active or account.is_suspended:
            raise ValidationError({"role": "Grant staff access to an active account before changing its staff role."})
        if account.pk == request.user.pk:
            raise ValidationError({"role": "You cannot change your own staff role."})
        if account.is_superuser and role != User.Role.SUPER_ADMIN and User.objects.filter(is_superuser=True, is_active=True).count() <= 1:
            raise ValidationError({"role": "Tarmac must keep at least one active superadmin."})

        account.role = self.STAFF_ROLES[role]
        account.is_superuser = role == User.Role.SUPER_ADMIN
        if account.is_superuser:
            account.staff_notification_sections = []
        account.save(update_fields=("role", "is_superuser", "staff_notification_sections"))
        for outstanding in OutstandingToken.objects.filter(user=account):
            BlacklistedToken.objects.get_or_create(token=outstanding)
        AdminLog.objects.create(actor=request.user, action="administrator_role_changed", target_type="User", target_id=str(account.pk), metadata={"role": role, "staff_notification_sections": account.staff_notification_sections})
        event_key = f"administrator-role:{account.pk}:{role}:{request.user.pk}:{timezone.now().timestamp()}"
        transaction.on_commit(lambda: notify_user(account, "Tarmac staff role updated", f"A Tarmac superadmin changed your staff role to {role.replace('_', ' ')}. You may need to sign in again.", {"kind": "account_enforcement", "notification_category": "account_security", "staff_role": role}, event_key))
        return Response(self.get_serializer(account).data)

    @action(detail=True, methods=["patch"], url_path="notification-sections")
    @transaction.atomic
    def notification_sections(self, request, pk=None):
        account = get_object_or_404(User.objects.select_for_update(), pk=pk, is_staff=True)
        if account.is_superuser:
            raise ValidationError({"staff_notification_sections": "Superadmins automatically receive alerts for every section."})
        sections = request.data.get("staff_notification_sections")
        from apps.notifications.staff import valid_staff_sections
        if not valid_staff_sections(sections):
            raise ValidationError({"staff_notification_sections": "Choose valid notification sections."})
        account.staff_notification_sections = sections
        account.save(update_fields=("staff_notification_sections",))
        AdminLog.objects.create(actor=request.user, action="staff_notification_sections_updated", target_type="User", target_id=str(account.pk), metadata={"staff_notification_sections": sections})
        return Response(self.get_serializer(account).data)

    @action(detail=True, methods=["post"], url_path="revoke")
    @transaction.atomic
    def revoke(self, request, pk=None):
        account = get_object_or_404(User.objects.select_for_update(), pk=pk)
        if not account.is_staff:
            raise ValidationError({"detail": "This account no longer has administrator access."})
        if account.pk == request.user.pk:
            raise ValidationError({"detail": "You cannot revoke your own administrator access."})
        if account.is_superuser and User.objects.filter(is_superuser=True, is_active=True).count() <= 1:
            raise ValidationError({"detail": "Tarmac must keep at least one active superadmin."})

        account.is_staff = False
        account.is_superuser = False
        account.role = User.Role.STUDENT
        account.staff_notification_sections = []
        account.save(update_fields=("is_staff", "is_superuser", "role", "staff_notification_sections"))
        for outstanding in OutstandingToken.objects.filter(user=account):
            BlacklistedToken.objects.get_or_create(token=outstanding)
        AdminLog.objects.create(actor=request.user, action="administrator_access_revoked", target_type="User", target_id=str(account.pk))
        event_key = f"administrator-access:{account.pk}:revoked:{request.user.pk}:{timezone.now().timestamp()}"
        transaction.on_commit(lambda: notify_user(account, "Tarmac administrator access revoked", "A Tarmac superuser removed administrator access from your account. Your account remains active with its existing student permissions.", {"kind": "account_enforcement", "notification_category": "account_security"}, event_key))
        return Response(self.get_serializer(account).data)


class PlatformSettingsView(generics.RetrieveUpdateAPIView):
    serializer_class = PlatformSettingsSerializer
    permission_classes = [IsSuperUser]
    http_method_names = ["get", "patch", "head", "options"]
    def get_object(self):
        return PlatformSettings.get_solo()
    def perform_update(self, serializer):
        settings_obj = serializer.save()
        changed_fields = tuple(key for key in serializer.validated_data if key != "site_logo")
        AdminLog.objects.create(actor=self.request.user, action="platform_settings_updated", target_type="PlatformSettings", target_id="1", metadata={key: getattr(settings_obj, key) for key in changed_fields})


class PublicEmergencyContactListView(generics.ListAPIView):
    serializer_class = EmergencyContactSerializer
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def get_queryset(self):
        campus = self.request.query_params.get("campus", "").strip()[:120]
        if not campus:
            return EmergencyContact.objects.none()
        return EmergencyContact.objects.filter(is_active=True, campus__iexact=campus).only(
            "id", "campus", "name", "phone_number", "description", "location", "sort_order", "is_active", "updated_at"
        )


class AdminEmergencyContactViewSet(viewsets.ModelViewSet):
    serializer_class = EmergencyContactSerializer
    permission_classes = [IsAdminOrAbove]
    queryset = EmergencyContact.objects.all()
    search_fields = ("name", "phone_number", "location", "description")
    ordering_fields = ("name", "sort_order", "updated_at")

    def perform_create(self, serializer):
        contact = serializer.save()
        AdminLog.objects.create(actor=self.request.user, action="emergency_contact_created", target_type="EmergencyContact", target_id=str(contact.pk), metadata={"name": contact.name, "active": contact.is_active})

    def perform_update(self, serializer):
        contact = serializer.save()
        AdminLog.objects.create(actor=self.request.user, action="emergency_contact_updated", target_type="EmergencyContact", target_id=str(contact.pk), metadata={"name": contact.name, "active": contact.is_active})

    def perform_destroy(self, instance):
        AdminLog.objects.create(actor=self.request.user, action="emergency_contact_deleted", target_type="EmergencyContact", target_id=str(instance.pk), metadata={"name": instance.name})
        instance.delete()

class AdminPaymentViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = AdminPaymentSerializer
    permission_classes = [IsAdminOrAbove]
    def get_queryset(self):
        return Payment.objects.select_related("user", "product", "advertisement", "subscription_plan").order_by("-created_at")

    @action(detail=False, methods=("get",), url_path="advertisement-summary")
    def advertisement_summary(self, request):
        groups = Payment.objects.filter(advertisement__isnull=False).values("status", "refund_status").annotate(total=Sum("amount_kobo"), count=models.Count("pk"))
        summary = {"gross_paid_kobo": 0, "refunded_kobo": 0, "net_revenue_kobo": 0, "successful_count": 0, "pending_count": 0, "failed_count": 0}
        for row in groups:
            amount = row["total"] or 0
            if row["status"] == Payment.Status.SUCCESS:
                summary["gross_paid_kobo"] += amount
                summary["successful_count"] += row["count"]
                if row["refund_status"] == Payment.RefundStatus.PROCESSED:
                    summary["refunded_kobo"] += amount
            elif row["status"] == Payment.Status.INITIALIZED:
                summary["pending_count"] += row["count"]
            elif row["status"] == Payment.Status.FAILED:
                summary["failed_count"] += row["count"]
        summary["net_revenue_kobo"] = summary["gross_paid_kobo"] - summary["refunded_kobo"]
        return Response(summary)

    @action(detail=True, methods=("post",), url_path="refund")
    @transaction.atomic
    def refund(self, request, pk=None):
        payment = Payment.objects.select_for_update().get(pk=pk)
        if payment.status != Payment.Status.SUCCESS:
            raise ValidationError({"payment": "Only a successful payment can be refunded."})
        if payment.refund_status:
            raise ValidationError({"refund_status": "A refund already exists for this payment. Use Retry refund only if Paystack marked it failed."})
        reason = str(request.data.get("reason", "")).strip()
        if len(reason) < 5:
            raise ValidationError({"reason": "Enter a clear reason of at least 5 characters."})
        from apps.payments.refunds import request_paystack_refund
        result = request_paystack_refund(payment, reason=reason, actor=request.user)
        AdminLog.objects.create(actor=request.user, action="payment_refund_requested", target_type="Payment", target_id=str(payment.pk), metadata={"refund_status": result["status"], "reason": reason[:200], "detail": result["detail"]})
        payment.refresh_from_db()
        return Response({"refund_status": payment.refund_status, "refund_reference": payment.refund_reference, "detail": result["detail"]})

    @action(detail=True, methods=("post",), url_path="retry-refund")
    @transaction.atomic
    def retry_refund(self, request, pk=None):
        payment = Payment.objects.select_for_update().get(pk=pk)
        if payment.status != Payment.Status.SUCCESS:
            raise ValidationError({"payment": "Only a successful payment can be refunded."})
        if payment.refund_status != Payment.RefundStatus.FAILED:
            raise ValidationError({"refund_status": "Only a failed refund can be retried."})
        reason = str(request.data.get("reason", "")).strip() or "Administrator retry of a failed Paystack refund."
        from apps.payments.refunds import request_paystack_refund
        result = request_paystack_refund(payment, reason=reason, actor=request.user)
        AdminLog.objects.create(actor=request.user, action="payment_refund_retried", target_type="Payment", target_id=str(payment.pk), metadata={"refund_status": result["status"], "detail": result["detail"]})
        payment.refresh_from_db()
        return Response({"refund_status": payment.refund_status, "refund_reference": payment.refund_reference, "detail": result["detail"]})

    @action(detail=True, methods=("post",), url_path="verify-paystack")
    def verify_paystack(self, request, pk=None):
        payment = self.get_object()
        if payment.status == Payment.Status.SUCCESS:
            return Response({"status": payment.status, "detail": "Payment was already confirmed."})
        from apps.payments.provider import PaystackVerificationError, verify_paystack_transaction
        from apps.payments.settlement import confirm_successful_payment
        try:
            provider_data = verify_paystack_transaction(payment.reference)
        except PaystackVerificationError as exc:
            raise exc
        if provider_data.get("status") == "success":
            _, result = confirm_successful_payment(payment.reference, provider_data)
            if result == "mismatch":
                raise ValidationError({"payment": "Paystack returned payment details that do not match this charge."})
        elif provider_data.get("status") == "failed":
            Payment.objects.filter(pk=payment.pk, status=Payment.Status.INITIALIZED).update(status=Payment.Status.FAILED, provider_response={"status": provider_data.get("status"), "gateway_response": provider_data.get("gateway_response")})
        payment.refresh_from_db()
        AdminLog.objects.create(actor=request.user, action="payment_reconciled_with_paystack", target_type="Payment", target_id=str(payment.pk), metadata={"provider_status": provider_data.get("status"), "payment_status": payment.status})
        return Response({"status": payment.status, "provider_status": provider_data.get("status")})

class AdminLostFoundViewSet(viewsets.ModelViewSet):
    serializer_class = AdminLostFoundSerializer
    permission_classes = [IsModeratorOrAbove]
    http_method_names = ["get", "patch", "head", "options"]
    def get_queryset(self):
        return LostFoundReport.objects.select_related("reporter", "category").order_by("-created_at")
    def perform_update(self, serializer):
        old_status = serializer.instance.status
        next_status = serializer.validated_data.get("status", old_status)
        if next_status == LostFoundReport.Status.REJECTED and not serializer.validated_data.get("reviewer_note", serializer.instance.reviewer_note).strip():
            raise ValidationError({"reviewer_note": "Add a reason so the student knows why this report was rejected."})
        if next_status == LostFoundReport.Status.OPEN:
            serializer.validated_data["is_active"] = True
        elif next_status in (LostFoundReport.Status.REJECTED, LostFoundReport.Status.CLOSED, LostFoundReport.Status.RETURNED):
            serializer.validated_data["is_active"] = False
        report = serializer.save()
        AdminLog.objects.create(actor=self.request.user, action="lost_found_report_moderated", target_type="LostFoundReport", target_id=str(report.pk), metadata={"status": report.status, "is_active": report.is_active})
        if report.status != old_status and report.status in (LostFoundReport.Status.OPEN, LostFoundReport.Status.REJECTED):
            approved = report.status == LostFoundReport.Status.OPEN
            subject = "Your Lost & Found report was approved" if approved else "Your Lost & Found report needs changes"
            body = f'“{report.title}” is now visible to students.' if approved else f'“{report.title}” was not approved. Reason: {report.reviewer_note}'
            notify_user(report.reporter, subject, body, {"kind": "lost_found_review", "report_id": report.pk, "status": report.status, "link": "/lost-found", "notification_category": "lost_found"}, f"lost-found-review:{report.pk}:{report.status}", dispatch_email=True)
            if approved:
                create_possible_matches(report)
    @action(detail=True, methods=("get",), url_path="image")
    def image(self, request, pk=None):
        report = self.get_object()
        if not report.image:
            from rest_framework.exceptions import NotFound
            raise NotFound("This report has no image.")
        return optimized_image_response(report.image)

class AdminItemClaimViewSet(viewsets.ModelViewSet):
    serializer_class = AdminItemClaimSerializer
    permission_classes = [IsModeratorOrAbove]
    http_method_names = ["get", "patch", "head", "options"]
    def get_queryset(self): return ItemClaim.objects.select_related("report", "claimant", "reviewed_by").order_by("status", "created_at")
    @transaction.atomic
    def perform_update(self, serializer):
        claim = ItemClaim.objects.select_for_update().select_related("report", "claimant").get(pk=serializer.instance.pk)
        if claim.status != ItemClaim.Status.PENDING:
            raise ValidationError({"status": "This claim has already been reviewed."})
        status_value = serializer.validated_data.get("status")
        if status_value not in (ItemClaim.Status.APPROVED, ItemClaim.Status.REJECTED):
            raise ValidationError({"status": "Choose approved or rejected."})
        claim.status = status_value
        claim.reviewer_note = serializer.validated_data.get("reviewer_note", "")
        claim.reviewed_by = self.request.user
        claim.reviewed_at = timezone.now()
        claim.save(update_fields=("status", "reviewer_note", "reviewed_by", "reviewed_at"))
        AdminLog.objects.create(actor=self.request.user, action=f"item_claim_{status_value}", target_type="ItemClaim", target_id=str(claim.pk), metadata={"report_id": claim.report_id})
        notify_user(claim.claimant, "Your found-item claim was reviewed", "Your claim was approved." if status_value == ItemClaim.Status.APPROVED else "Your claim was not approved. Sign in to review the administrator's note.", {"kind":"item_claim_review", "claim_id":claim.pk,"status":status_value}, f"item-claim-reviewed:{claim.pk}")
        serializer.instance = claim

class AdminItemMatchViewSet(viewsets.ModelViewSet):
    serializer_class = AdminItemMatchSerializer
    permission_classes = [IsModeratorOrAbove]
    http_method_names = ["get", "patch", "head", "options"]
    filterset_fields = ["is_reviewed", "is_dismissed"]
    def get_queryset(self): return ItemMatch.objects.select_related("lost_report__reporter", "found_report__reporter").order_by("-score", "-created_at")
    def perform_update(self, serializer):
        match = serializer.save()
        AdminLog.objects.create(actor=self.request.user, action="item_match_reviewed" if match.is_reviewed else "item_match_dismissed", target_type="ItemMatch", target_id=str(match.pk), metadata={"is_dismissed":match.is_dismissed})
        if match.is_reviewed and not match.is_dismissed:
            match = ItemMatch.objects.select_related("lost_report__reporter", "found_report__reporter").get(pk=match.pk)
            for owner, title in ((match.lost_report.reporter, match.lost_report.title), (match.found_report.reporter, match.found_report.title)):
                from apps.notifications.services import send_official_match_message
                conversation = send_official_match_message(match, owner, title, confirmed=True)
                notify_user(owner, "A possible lost-and-found match was confirmed", f'An administrator confirmed a possible match for "{title}". Open the official Tarmac message to review it.', {"kind":"lost_found_match", "match_id":match.pk,"report_title":title,"conversation_id":conversation.pk if conversation else None,"link":f"/messages?conversation={conversation.pk}" if conversation else "/lost-found","notification_category":"lost_found"}, f"admin-confirmed-match:{match.pk}:{owner.pk}")

class AdminProductViewSet(viewsets.ModelViewSet):
    serializer_class = AdminProductSerializer
    permission_classes = [IsModeratorOrAbove]
    http_method_names = ["get", "patch", "head", "options"]
    def get_queryset(self):
        Product.objects.filter(status=Product.Status.ACTIVE, expires_at__lte=timezone.now()).update(status=Product.Status.EXPIRED)
        return Product.objects.select_related("seller", "category").prefetch_related("images").order_by("-created_at")
    @action(detail=True, methods=("get",), url_path="image")
    def image(self, request, pk=None):
        product = self.get_object()
        image = product.images.first()
        if not image:
            from rest_framework.exceptions import NotFound
            raise NotFound("This listing has no image.")
        return optimized_image_response(image.image)
    @transaction.atomic
    def perform_update(self, serializer):
        product = Product.objects.select_for_update().select_related("seller").get(pk=serializer.instance.pk)
        next_status = serializer.validated_data.get("status", product.status)
        if "is_featured" in serializer.validated_data:
            product.is_featured = serializer.validated_data["is_featured"]
            product.featured_until = timezone.now() + timedelta(days=product.sponsorship_duration_days if product.sponsor_requested else PlatformSettings.get_solo().sponsored_product_duration_days) if product.is_featured else None
        if "featured_position" in serializer.validated_data:
            product.featured_position = serializer.validated_data["featured_position"]
            AdminLog.objects.create(actor=self.request.user, action="marketplace_listing_featured" if product.is_featured else "marketplace_listing_unfeatured", target_type="Product", target_id=str(product.pk), metadata={"seller_id": product.seller_id})
        if next_status == Product.Status.PENDING_REVIEW and product.status == Product.Status.REMOVED:
            product.status = Product.Status.PENDING_REVIEW
            product.save(update_fields=("status", "is_featured", "featured_until", "featured_position", "updated_at"))
            AdminLog.objects.create(actor=self.request.user, action="marketplace_listing_restored_for_review", target_type="Product", target_id=str(product.pk), metadata={"seller_id": product.seller_id})
            serializer.instance = product
            return
        if next_status == Product.Status.ACTIVE:
            platform = PlatformSettings.get_solo()
            payment_required = platform.paystack_enabled and ((platform.listing_fee_enabled and platform.listing_fee_kobo > 0) or (product.sponsor_requested and platform.sponsored_product_fee_enabled and product.sponsorship_fee_kobo > 0))
            paid = Payment.objects.filter(product=product, status=Payment.Status.SUCCESS).exists()
            if product.status != Product.Status.PENDING_REVIEW:
                raise ValidationError({"status": "Only listings pending review can be approved."})
            if payment_required and not paid:
                raise ValidationError({"payment": "A successful listing payment is required before approval."})
            product.status = Product.Status.ACTIVE
            product.expires_at = timezone.now() + timedelta(days=30)
            if product.sponsor_requested and platform.sponsored_products_enabled:
                product.is_featured = True
                product.featured_until = timezone.now() + timedelta(days=product.sponsorship_duration_days)
            product.save(update_fields=("status", "expires_at", "is_featured", "featured_until", "featured_position", "updated_at"))
        elif next_status == Product.Status.REMOVED:
            product.status = Product.Status.REMOVED
            product.save(update_fields=("status", "is_featured", "featured_until", "featured_position", "updated_at"))
        elif next_status == product.status:
            product.save(update_fields=("is_featured", "featured_until", "featured_position", "updated_at"))
        else:
            raise ValidationError({"status": "Choose active to approve or removed to reject a pending listing."})
        AdminLog.objects.create(actor=self.request.user, action=f"marketplace_listing_{product.status}", target_type="Product", target_id=str(product.pk), metadata={"seller_id": product.seller_id})
        serializer.instance = product

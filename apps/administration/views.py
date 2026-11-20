from datetime import timedelta
import uuid
import mimetypes
from django.db import transaction
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from rest_framework import viewsets,permissions, status, generics
from rest_framework.decorators import action, api_view, permission_classes as drf_permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from django.db import models
from django.db.models import Count, Sum
from django.db.models.functions import TruncDate
from django.db.models import Prefetch
from django.utils import timezone
from apps.accounts.permissions import IsVerifiedStudent
from apps.accounts.permissions import is_student_feature_eligible
from apps.accounts.models import User, ProfileChangeRequest, ProfileChangeMessage
from apps.accounts.models import StudentVerification
from apps.reviews.models import SellerReview
from apps.products.models import Product
from apps.payments.models import Payment
from apps.lost_found.models import LostFoundReport, ItemMatch, ItemClaim
from apps.ads.models import AdCampaign
from apps.messaging.models import Conversation, Message
from apps.messaging.serializers import MessageSerializer
from .models import UserReport,ReportEvidence,Announcement,AnnouncementBookmark,AdminLog,PlatformSettings,SupportTicket,SupportMessage,SupportAttachment,SubscriptionPlan,StudentSubscription
from .serializers import UserReportSerializer,AnnouncementSerializer,AdminLogSerializer,AdminVerificationSerializer,AdminReviewSerializer,AdminAccountSerializer,AdminConversationSerializer,SubscriptionPlanSerializer,StudentSubscriptionSerializer,SupportTicketSerializer,PlatformSettingsSerializer,AdminPaymentSerializer,AdminLostFoundSerializer,AdminItemClaimSerializer,AdminItemMatchSerializer,AdminProductSerializer
from apps.accounts.serializers import AdminProfileChangeRequestSerializer, ProfileUpdateSerializer
from apps.notifications.services import notify_user
class UserReportViewSet(viewsets.ModelViewSet):
    serializer_class=UserReportSerializer
    http_method_names = ["get", "post", "patch", "head", "options"]
    filterset_fields = ["is_resolved", "reason"]

    def get_permissions(self):
        if self.request.user and self.request.user.is_staff:
            return [permissions.IsAdminUser()]
        return [IsVerifiedStudent()]

    def get_queryset(self):
        queryset = UserReport.objects.select_related("reporter", "reported_user", "product", "lost_found_report").prefetch_related("evidence")
        return queryset if self.request.user.is_staff else queryset.filter(reporter=self.request.user)

    def perform_create(self,serializer):
        report = serializer.save(reporter=self.request.user)
        if PlatformSettings.get_solo().report_notifications_enabled:
            for administrator in User.objects.filter(is_staff=True, is_active=True):
                notify_user(administrator, "New student report", f"A student submitted report #{report.pk}. Open the admin report queue to review it.", {"kind":"user_report", "report_id":report.pk}, f"user-report:{report.pk}:admin:{administrator.pk}")

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
class AnnouncementViewSet(viewsets.ModelViewSet):
    serializer_class=AnnouncementSerializer
    def get_queryset(self):
        if self.request.user.is_staff:
            return Announcement.objects.select_related("created_by").all()
        return Announcement.objects.none() if not PlatformSettings.get_solo().announcements_enabled else Announcement.objects.filter(is_published=True).filter(
            models.Q(audience=Announcement.Audience.ALL_VERIFIED) |
            models.Q(audience=Announcement.Audience.DEPARTMENT, department__iexact=self.request.user.department)
        ).select_related("created_by")
    def get_permissions(self):
        if self.action in ("saved", "toggle_saved"):
            return [IsVerifiedStudent()]
        if self.action in ("list", "retrieve"):
            if self.request.user and self.request.user.is_staff:
                return [permissions.IsAdminUser()]
            return [IsVerifiedStudent()]
        return [permissions.IsAdminUser()]
    @action(detail=False, methods=["get"])
    def saved(self, request):
        ids = AnnouncementBookmark.objects.filter(user=request.user, announcement__is_published=True).values_list("announcement_id", flat=True)
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
        if audience == Announcement.Audience.DEPARTMENT and not serializer.validated_data.get("department", "").strip():
            raise ValidationError({"department": "Choose a department for a department announcement."})
        serializer.save(created_by=self.request.user)
    @action(detail=True, methods=["post"])
    def publish(self, request, pk=None):
        if not PlatformSettings.get_solo().announcements_enabled:
            raise ValidationError("Announcements are currently disabled in platform settings.")
        announcement = self.get_object()
        if announcement.is_published:
            return Response({"detail": "This announcement has already been published."}, status=status.HTTP_409_CONFLICT)
        now = timezone.now()
        announcement.is_published = True
        announcement.published_at = now
        announcement.save(update_fields=("is_published", "published_at"))
        AdminLog.objects.create(actor=request.user, action="announcement_published", target_type="Announcement", target_id=str(announcement.pk), metadata={"audience":announcement.audience,"department":announcement.department})
        return Response({"published": True, "delivery_queued": True})
    @action(detail=True, methods=["patch"])
    def pin(self, request, pk=None):
        announcement = self.get_object()
        announcement.pinned = bool(request.data.get("pinned", not announcement.pinned))
        announcement.save(update_fields=("pinned",))
        AdminLog.objects.create(actor=request.user, action="announcement_pinned" if announcement.pinned else "announcement_unpinned", target_type="Announcement", target_id=str(announcement.pk), metadata={})
        return Response({"pinned": announcement.pinned})
class AdminLogViewSet(viewsets.ReadOnlyModelViewSet): queryset=AdminLog.objects.all(); serializer_class=AdminLogSerializer; permission_classes=[permissions.IsAdminUser]

@api_view(["GET"])
@drf_permission_classes([permissions.IsAuthenticated])
def report_evidence_image(request, evidence_id):
    evidence = get_object_or_404(ReportEvidence.objects.select_related("report"), pk=evidence_id)
    if not request.user.is_staff and evidence.report.reporter_id != request.user.pk:
        from rest_framework.exceptions import PermissionDenied
        raise PermissionDenied("You cannot view evidence attached to another student's report.")
    content_type = mimetypes.guess_type(evidence.image.name)[0] or "application/octet-stream"
    return FileResponse(evidence.image.open("rb"), content_type=content_type)

class AdminVerificationViewSet(viewsets.ModelViewSet):
    serializer_class = AdminVerificationSerializer
    permission_classes = [permissions.IsAdminUser]
    http_method_names = ["get", "patch", "head", "options"]

    def get_queryset(self):
        queryset = StudentVerification.objects.select_related("user", "reviewed_by").order_by("status", "created_at")
        status_filter = self.request.query_params.get("status")
        return queryset.filter(status=status_filter) if status_filter else queryset

    @transaction.atomic
    def perform_update(self, serializer):
        verification = StudentVerification.objects.select_for_update().select_related("user").get(pk=serializer.instance.pk)
        serializer.instance = verification
        if verification.status != StudentVerification.Status.PENDING:
            raise ValidationError({"status": "This verification request has already been reviewed."})
        status_value = serializer.validated_data["status"]
        user = User.objects.select_for_update().get(pk=verification.user_id)
        if status_value == StudentVerification.Status.APPROVED:
            if PlatformSettings.get_solo().email_verification_required and not user.email_verified:
                raise ValidationError({"status": "The student must verify their email before approval."})
            duplicate = User.objects.filter(matric_number=verification.submitted_matric_number).exclude(pk=user.pk).exists()
            if duplicate:
                raise ValidationError({"status": "This matric number is already assigned to another account."})
            user.matric_number = verification.submitted_matric_number
            user.is_student_verified = True
            user.save(update_fields=("matric_number", "is_student_verified"))
        else:
            user.is_student_verified = False
            user.save(update_fields=("is_student_verified",))
        verification.status = status_value
        verification.reviewer_note = serializer.validated_data.get("reviewer_note", verification.reviewer_note)
        verification.reviewed_by = self.request.user
        verification.reviewed_at = timezone.now()
        verification.save(update_fields=("status", "reviewer_note", "reviewed_by", "reviewed_at"))
        AdminLog.objects.create(actor=self.request.user, action=f"student_verification_{status_value}", target_type="StudentVerification", target_id=str(verification.pk), metadata={"user_id": user.pk})
        notify_user(user, "Student verification reviewed", "Your student verification was approved." if status_value == StudentVerification.Status.APPROVED else "Your student verification needs attention. Sign in to review the school's note.", {"kind": "verification_review", "status": status_value}, f"verification-reviewed:{verification.pk}:{status_value}")

    @action(detail=True, methods=["get"], url_path="student-id")
    def student_id(self, request, pk=None):
        verification = get_object_or_404(StudentVerification, pk=pk)
        if not verification.student_id_image:
            from rest_framework.exceptions import NotFound
            raise NotFound("No student ID image was submitted.")
        content_type = mimetypes.guess_type(verification.student_id_image.name)[0] or "application/octet-stream"
        return FileResponse(verification.student_id_image.open("rb"), content_type=content_type)

class AdminReviewViewSet(viewsets.ModelViewSet):
    queryset = SellerReview.objects.select_related("seller", "reviewer", "order").all()
    serializer_class = AdminReviewSerializer
    permission_classes = [permissions.IsAdminUser]
    http_method_names = ["get", "patch", "delete", "head", "options"]
    filterset_fields = ["status", "rating"]

    def perform_update(self, serializer):
        review = serializer.save()
        AdminLog.objects.create(actor=self.request.user, action=f"seller_review_{review.status}", target_type="SellerReview", target_id=str(review.pk), metadata={"seller_id": review.seller_id})

class AdminAccountViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all().order_by("-date_joined")
    serializer_class = AdminAccountSerializer
    permission_classes = [permissions.IsAdminUser]
    http_method_names = ["get", "patch", "head", "options"]
    search_fields = ["email", "username", "matric_number"]

    def perform_update(self, serializer):
        account = serializer.save()
        if account.is_suspended or not account.is_active:
            for outstanding in OutstandingToken.objects.filter(user=account):
                BlacklistedToken.objects.get_or_create(token=outstanding)
        AdminLog.objects.create(actor=self.request.user, action="student_account_updated", target_type="User", target_id=str(account.pk), metadata={"is_active": account.is_active, "is_suspended": account.is_suspended})


class AdminProfileChangeRequestViewSet(viewsets.ModelViewSet):
    serializer_class = AdminProfileChangeRequestSerializer
    permission_classes = [permissions.IsAdminUser]
    http_method_names = ["get", "patch", "head", "options"]
    filterset_fields = ["status"]

    def get_queryset(self):
        return ProfileChangeRequest.objects.select_related("user", "reviewed_by").prefetch_related("messages__sender").all()

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
        notify_user(change_request.user, "Profile change request reviewed", "Your profile change request was " + ("approved and applied." if next_status == ProfileChangeRequest.Status.APPROVED else "declined. Open your profile to see the review conversation."), {"kind": "profile_change_review", "request_id": change_request.pk, "status": next_status}, f"profile-change-review:{change_request.pk}:{next_status}")
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
        notify_user(change_request.user, "Admin has a question about your profile request", body, {"kind": "profile_change_question", "request_id": change_request.pk}, f"profile-change-question:{change_request.pk}:{change_request.messages.count()}")
        return Response(self.get_serializer(change_request).data)

    @action(detail=True, methods=["get"], url_path="image")
    def image(self, request, pk=None):
        change_request = self.get_object()
        if not change_request.requested_profile_picture:
            from rest_framework.exceptions import NotFound
            raise NotFound("No profile picture was included in this request.")
        content_type = mimetypes.guess_type(change_request.requested_profile_picture.name)[0] or "application/octet-stream"
        return FileResponse(change_request.requested_profile_picture.open("rb"), content_type=content_type)

class AdminConversationViewSet(viewsets.GenericViewSet):
    serializer_class = AdminConversationSerializer
    permission_classes = [permissions.IsAdminUser]
    http_method_names = ["get", "patch", "post", "head", "options"]
    def get_queryset(self):
        latest_messages = Message.objects.select_related("sender").prefetch_related("attachments").order_by("-created_at")[:50]
        return Conversation.objects.annotate(unread_count=Count("messages", filter=models.Q(messages__sender__is_staff=False, messages__read_at__isnull=True))).prefetch_related("participants", Prefetch("messages", queryset=latest_messages, to_attr="recent_messages")).order_by("-updated_at")
    def list(self, request):
        queryset = self.get_queryset()
        page = self.paginate_queryset(queryset)
        return self.get_paginated_response(self.get_serializer(page, many=True).data) if page is not None else Response(self.get_serializer(queryset, many=True).data)
    def retrieve(self, request, pk=None): return Response(self.get_serializer(self.get_object()).data)
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
            notify_user(student, "New message from Tarmac administration", body[:240], {"kind":"admin_conversation_reply", "conversation_id":conversation.pk}, f"admin-conversation-reply:{message.pk}:{student.pk}")
        AdminLog.objects.create(actor=request.user, action="conversation_admin_reply", target_type="Conversation", target_id=str(conversation.pk), metadata={"message_id":message.pk})
        conversation = self.get_queryset().get(pk=conversation.pk)
        return Response(AdminConversationSerializer(conversation, context={"request": request}).data, status=status.HTTP_201_CREATED)

class SubscriptionPlanViewSet(viewsets.ModelViewSet):
    serializer_class = SubscriptionPlanSerializer
    def get_queryset(self):
        queryset = SubscriptionPlan.objects.all()
        return queryset if self.request.user.is_staff else queryset.filter(is_active=True)
    def get_permissions(self): return [permissions.AllowAny()] if self.action in ("list", "retrieve") else [permissions.IsAdminUser()]
    def perform_create(self, serializer):
        plan = serializer.save(); AdminLog.objects.create(actor=self.request.user, action="subscription_plan_created", target_type="SubscriptionPlan", target_id=str(plan.pk), metadata={})
    def perform_update(self, serializer):
        plan = serializer.save(); AdminLog.objects.create(actor=self.request.user, action="subscription_plan_updated", target_type="SubscriptionPlan", target_id=str(plan.pk), metadata={})

class StudentSubscriptionViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = StudentSubscriptionSerializer
    def get_permissions(self): return [permissions.IsAdminUser()] if self.request.user.is_staff else [IsVerifiedStudent()]
    def get_queryset(self):
        queryset = StudentSubscription.objects.select_related("user", "plan")
        return queryset if self.request.user.is_staff else queryset.filter(user=self.request.user)

@api_view(["GET"])
@drf_permission_classes([permissions.IsAdminUser])
def dashboard(request):
    today = timezone.localdate()
    month_start = today.replace(day=1)
    week_start = today - timedelta(days=6)
    payments = Payment.objects.filter(status=Payment.Status.SUCCESS)
    return Response({
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
            "pending_reviews": SellerReview.objects.filter(status=SellerReview.Status.PENDING).count(),
        },
    })

@api_view(["GET"])
@drf_permission_classes([permissions.IsAdminUser])
def analytics(request):
    try:
        days = int(request.query_params.get("days", 30))
    except (TypeError, ValueError):
        raise ValidationError({"days": "Choose a period of 7, 30, or 90 days."})
    if days not in (7, 30, 90):
        raise ValidationError({"days": "Choose a period of 7, 30, or 90 days."})
    start = timezone.localdate() - timedelta(days=days - 1)
    registrations = {row["day"].isoformat(): row["total"] for row in User.objects.filter(date_joined__date__gte=start).annotate(day=TruncDate("date_joined")).values("day").annotate(total=Count("id"))}
    payments = {row["day"].isoformat(): row for row in Payment.objects.filter(status=Payment.Status.SUCCESS, paid_at__date__gte=start).annotate(day=TruncDate("paid_at")).values("day").annotate(count=Count("id"), revenue_kobo=Sum("amount_kobo"))}
    lost_found = {row["day"].isoformat(): row["total"] for row in LostFoundReport.objects.filter(created_at__date__gte=start).annotate(day=TruncDate("created_at")).values("day").annotate(total=Count("id"))}
    series = []
    for offset in range(days):
        day = (start + timedelta(days=offset)).isoformat()
        payment = payments.get(day, {})
        series.append({"date": day, "registrations": registrations.get(day, 0), "payments": payment.get("count", 0), "revenue_kobo": payment.get("revenue_kobo") or 0, "lost_found_reports": lost_found.get(day, 0)})
    return Response({"days": days, "series": series, "totals": {"registrations": sum(row["registrations"] for row in series), "payments": sum(row["payments"] for row in series), "revenue_kobo": sum(row["revenue_kobo"] for row in series), "lost_found_reports": sum(row["lost_found_reports"] for row in series)}})

@api_view(["POST"])
@drf_permission_classes([permissions.IsAdminUser])
@transaction.atomic
def send_admin_message(request):
    email = str(request.data.get("email", "")).strip().lower()
    subject = str(request.data.get("subject", "")).strip()
    body = str(request.data.get("body", "")).strip()
    uploads = request.FILES.getlist("attachment_files")
    if not email or not subject or (not body and not uploads):
        raise ValidationError({"detail": "Student email and title are required, with a message or attachment."})
    if len(subject) > 180 or len(body) > 5000:
        raise ValidationError({"detail": "Message title must be 180 characters or fewer and message 5,000 or fewer."})
    user = User.objects.filter(email__iexact=email, is_active=True, is_suspended=False).first()
    if not is_student_feature_eligible(user):
        raise ValidationError({"email": "No active eligible student account was found with this email."})
    conversation = Conversation.objects.annotate(participant_count=Count("participants", distinct=True)).filter(
        context_type=Conversation.ContextType.OFFICIAL, is_official=True, participant_count=2, participants=request.user
    ).filter(participants=user).first()
    if conversation is None:
        conversation = Conversation.objects.create(context_type=Conversation.ContextType.OFFICIAL, context_title=subject[:180], is_official=True)
        conversation.participants.add(request.user, user)
    serializer = MessageSerializer(data={"conversation": conversation.pk, "body": f"{subject}\n\n{body}".strip(), "attachment_files": uploads}, context={"request": request})
    serializer.is_valid(raise_exception=True)
    message = serializer.save(sender=request.user)
    conversation.is_official = True
    conversation.context_title = subject[:180]
    conversation.save(update_fields=("is_official", "context_title", "updated_at"))
    notify_user(user, "Official message from Tarmac", body[:240] or subject, {"kind": "admin_conversation_reply", "conversation_id": conversation.pk, "is_official": True}, f"admin-message:{message.pk}:{user.pk}")
    AdminLog.objects.create(actor=request.user, action="student_message_sent", target_type="User", target_id=str(user.pk), metadata={"email": user.email})
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
        "maintenance_mode": config.maintenance_mode,
        "maintenance_message": config.maintenance_message if config.maintenance_mode else "",
        "features": {
            "marketplace": config.marketplace_enabled,
            "lost_found": config.lost_found_enabled,
            "messaging": config.messaging_enabled,
            "ads": config.ads_enabled,
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
    content_type = mimetypes.guess_type(config.site_logo.name)[0] or "application/octet-stream"
    return FileResponse(config.site_logo.open("rb"), content_type=content_type)

class SupportTicketViewSet(viewsets.ModelViewSet):
    serializer_class = SupportTicketSerializer
    permission_classes = [permissions.IsAdminUser | IsVerifiedStudent]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        queryset = SupportTicket.objects.select_related("user").prefetch_related("messages__sender", "messages__attachments")
        return queryset if self.request.user.is_staff else queryset.filter(user=self.request.user)

    def get_permissions(self):
        if self.action == "partial_update":
            return [permissions.IsAdminUser()]
        return super().get_permissions()

    def perform_create(self, serializer):
        ticket = serializer.save(user=self.request.user)
        from apps.notifications.services import notify_user
        for administrator in User.objects.filter(is_staff=True, is_active=True).exclude(pk=self.request.user.pk).iterator():
            notify_user(administrator, "New student support request", ticket.subject, {"kind": "support_ticket", "ticket_id": ticket.pk}, f"support-ticket:{ticket.pk}:{administrator.pk}")

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
            for administrator in User.objects.filter(is_staff=True, is_active=True).exclude(pk=request.user.pk).iterator():
                notify_user(administrator, "Student reopened a support request", ticket.subject, {"kind":"support_ticket_reopened", "ticket_id":ticket.pk}, f"support-ticket-reopen:{ticket.pk}:{ticket.updated_at.timestamp()}:{administrator.pk}")
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
            for administrator in User.objects.filter(is_staff=True, is_active=True).exclude(pk=request.user.pk).iterator():
                notify_user(administrator, "Student marked a support request complete", ticket.subject, {"kind": "support_completed", "ticket_id": ticket.pk}, f"support-completed:{ticket.pk}:{ticket.updated_at.timestamp()}:{administrator.pk}")
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
            if upload.size > 5 * 1024 * 1024 or upload.content_type not in ("image/jpeg", "image/png", "image/webp", "application/pdf"):
                raise ValidationError({"attachment_files": "Use JPG, PNG, WEBP or PDF files no larger than 5 MB."})
        if ticket.status == SupportTicket.Status.RESOLVED and not request.user.is_staff:
            raise ValidationError({"status": "This ticket is resolved. Ask an administrator to reopen it."})
        from django.utils.text import get_valid_filename
        with transaction.atomic():
            message = SupportMessage.objects.create(ticket=ticket, sender=request.user, body=body)
            for upload in uploads:
                SupportAttachment.objects.create(message=message, file=upload, original_name=get_valid_filename(upload.name)[:255], content_type=upload.content_type)
            if ticket.status == SupportTicket.Status.OPEN and request.user.is_staff:
                ticket.status = SupportTicket.Status.IN_PROGRESS
                ticket.save(update_fields=("status", "updated_at"))
        from apps.notifications.services import notify_user
        if request.user.is_staff:
            notify_user(ticket.user, f"Support replied: {ticket.subject}", body[:500], {"kind": "support_reply", "ticket_id": ticket.pk}, f"support-reply:{message.pk}:{ticket.user_id}")
        else:
            for administrator in User.objects.filter(is_staff=True, is_active=True).exclude(pk=request.user.pk).iterator():
                notify_user(administrator, f"Student replied: {ticket.subject}", body[:500], {"kind": "support_reply", "ticket_id": ticket.pk}, f"support-reply:{message.pk}:{administrator.pk}")
        ticket.refresh_from_db()
        return Response(SupportTicketSerializer(ticket, context={"request": request}).data)


@api_view(["GET"])
@drf_permission_classes([permissions.IsAuthenticated])
def support_attachment_file(request, attachment_id):
    attachment = get_object_or_404(SupportAttachment.objects.select_related("message__ticket"), pk=attachment_id)
    if not request.user.is_staff and attachment.message.ticket.user_id != request.user.pk:
        from rest_framework.exceptions import PermissionDenied
        raise PermissionDenied("You cannot view attachments for another student's support ticket.")
    return FileResponse(attachment.file.open("rb"), content_type=attachment.content_type)

class IsSuperUser(permissions.BasePermission):
    message = "Only a superuser can change platform-wide settings."
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_superuser)

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

class AdminPaymentViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = AdminPaymentSerializer
    permission_classes = [permissions.IsAdminUser]
    def get_queryset(self):
        return Payment.objects.select_related("user", "product", "advertisement", "subscription_plan").order_by("-created_at")

class AdminLostFoundViewSet(viewsets.ModelViewSet):
    serializer_class = AdminLostFoundSerializer
    permission_classes = [permissions.IsAdminUser]
    http_method_names = ["get", "patch", "head", "options"]
    def get_queryset(self):
        return LostFoundReport.objects.select_related("reporter", "category").order_by("-created_at")
    def perform_update(self, serializer):
        report = serializer.save()
        AdminLog.objects.create(actor=self.request.user, action="lost_found_report_moderated", target_type="LostFoundReport", target_id=str(report.pk), metadata={"status": report.status, "is_active": report.is_active})
    @action(detail=True, methods=("get",), url_path="image")
    def image(self, request, pk=None):
        report = self.get_object()
        if not report.image:
            from rest_framework.exceptions import NotFound
            raise NotFound("This report has no image.")
        content_type = mimetypes.guess_type(report.image.name)[0] or "application/octet-stream"
        return FileResponse(report.image.open("rb"), content_type=content_type)

class AdminItemClaimViewSet(viewsets.ModelViewSet):
    serializer_class = AdminItemClaimSerializer
    permission_classes = [permissions.IsAdminUser]
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
    permission_classes = [permissions.IsAdminUser]
    http_method_names = ["get", "patch", "head", "options"]
    filterset_fields = ["is_reviewed", "is_dismissed"]
    def get_queryset(self): return ItemMatch.objects.select_related("lost_report__reporter", "found_report__reporter").order_by("-score", "-created_at")
    def perform_update(self, serializer):
        match = serializer.save()
        AdminLog.objects.create(actor=self.request.user, action="item_match_reviewed" if match.is_reviewed else "item_match_dismissed", target_type="ItemMatch", target_id=str(match.pk), metadata={"is_dismissed":match.is_dismissed})
        if match.is_reviewed and not match.is_dismissed:
            match = ItemMatch.objects.select_related("lost_report__reporter", "found_report__reporter").get(pk=match.pk)
            for owner, title in ((match.lost_report.reporter, match.lost_report.title), (match.found_report.reporter, match.found_report.title)):
                notify_user(owner, "A possible lost-and-found match was confirmed", f'An administrator confirmed a possible match for "{title}". Open Lost & Found to contact the other student.', {"kind":"lost_found_match", "match_id":match.pk,"report_title":title}, f"admin-confirmed-match:{match.pk}:{owner.pk}")

class AdminProductViewSet(viewsets.ModelViewSet):
    serializer_class = AdminProductSerializer
    permission_classes = [permissions.IsAdminUser]
    http_method_names = ["get", "patch", "head", "options"]
    def get_queryset(self):
        return Product.objects.select_related("seller", "category").prefetch_related("images").order_by("-created_at")
    @action(detail=True, methods=("get",), url_path="image")
    def image(self, request, pk=None):
        product = self.get_object()
        image = product.images.first()
        if not image:
            from rest_framework.exceptions import NotFound
            raise NotFound("This listing has no image.")
        content_type = mimetypes.guess_type(image.image.name)[0] or "application/octet-stream"
        return FileResponse(image.image.open("rb"), content_type=content_type)
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

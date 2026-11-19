from datetime import timedelta
import uuid
import mimetypes
from django.db import transaction
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from rest_framework import viewsets,permissions, status
from rest_framework.decorators import action, api_view, permission_classes as drf_permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from django.db import models
from django.db.models import Count, Sum
from django.utils import timezone
from apps.accounts.permissions import IsVerifiedStudent
from apps.accounts.models import User
from apps.accounts.models import StudentVerification
from apps.reviews.models import SellerReview
from apps.products.models import Product
from apps.payments.models import Payment
from apps.lost_found.models import LostFoundReport, ItemMatch
from apps.ads.models import AdCampaign
from .models import UserReport,ReportEvidence,Announcement,AdminLog,PlatformSettings
from .serializers import UserReportSerializer,AnnouncementSerializer,AdminLogSerializer,AdminVerificationSerializer,AdminReviewSerializer,AdminAccountSerializer
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
        serializer.save(reporter=self.request.user)

    def perform_update(self, serializer):
        if not self.request.user.is_staff:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Only school administrators can update a report.")
        report = serializer.save()
        AdminLog.objects.create(actor=self.request.user, action="user_report_resolved" if report.is_resolved else "user_report_updated", target_type="UserReport", target_id=str(report.pk), metadata={})
class AnnouncementViewSet(viewsets.ModelViewSet):
    serializer_class=AnnouncementSerializer
    def get_queryset(self):
        if self.request.user.is_staff:
            return Announcement.objects.select_related("created_by").all()
        return Announcement.objects.filter(is_published=True).filter(
            models.Q(audience=Announcement.Audience.ALL_VERIFIED) |
            models.Q(audience=Announcement.Audience.DEPARTMENT, department__iexact=self.request.user.department)
        ).select_related("created_by")
    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            if self.request.user and self.request.user.is_staff:
                return [permissions.IsAdminUser()]
            return [IsVerifiedStudent()]
        return [permissions.IsAdminUser()]
    def perform_create(self, serializer):
        audience = serializer.validated_data.get("audience", Announcement.Audience.ALL_VERIFIED)
        if audience == Announcement.Audience.DEPARTMENT and not serializer.validated_data.get("department", "").strip():
            raise ValidationError({"department": "Choose a department for a department announcement."})
        serializer.save(created_by=self.request.user)
    @action(detail=True, methods=["post"])
    def publish(self, request, pk=None):
        announcement = self.get_object()
        if announcement.is_published:
            return Response({"detail": "This announcement has already been published."}, status=status.HTTP_409_CONFLICT)
        now = timezone.now()
        announcement.is_published = True
        announcement.published_at = now
        announcement.save(update_fields=("is_published", "published_at"))
        AdminLog.objects.create(actor=request.user, action="announcement_published", target_type="Announcement", target_id=str(announcement.pk), metadata={"audience":announcement.audience,"department":announcement.department})
        return Response({"published": True, "delivery_queued": True})
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
            if not user.email_verified:
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
        AdminLog.objects.create(actor=self.request.user, action="student_account_updated", target_type="User", target_id=str(account.pk), metadata={"is_active": account.is_active, "is_suspended": account.is_suspended})

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
    })

@api_view(["POST"])
@drf_permission_classes([permissions.IsAdminUser])
def send_admin_message(request):
    email = str(request.data.get("email", "")).strip().lower()
    subject = str(request.data.get("subject", "")).strip()
    body = str(request.data.get("body", "")).strip()
    if not email or not subject or not body:
        raise ValidationError({"detail": "Student email, title, and message are required."})
    if len(subject) > 180 or len(body) > 5000:
        raise ValidationError({"detail": "Message title must be 180 characters or fewer and message 5,000 or fewer."})
    user = User.objects.filter(email__iexact=email, is_active=True, is_suspended=False, is_student_verified=True).first()
    if user is None:
        raise ValidationError({"email": "No active verified student was found with this email."})
    notify_user(user, subject, body, {"kind": "admin_message", "display": "popup"}, f"admin-message:{uuid.uuid4().hex}")
    AdminLog.objects.create(actor=request.user, action="student_message_sent", target_type="User", target_id=str(user.pk), metadata={"email": user.email})
    return Response({"sent": True, "student": user.email})

from rest_framework.permissions import AllowAny

@api_view(["GET"])
@drf_permission_classes([AllowAny])
def system_status(request):
    config = PlatformSettings.get_solo()
    return Response({
        "maintenance_mode": config.maintenance_mode,
        "maintenance_message": config.maintenance_message if config.maintenance_mode else "",
        "features": {
            "marketplace": config.marketplace_enabled,
            "lost_found": config.lost_found_enabled,
            "messaging": config.messaging_enabled,
            "ads": config.ads_enabled,
        },
        "fees": {
            "listing_fee_required": config.listing_fee_enabled and config.paystack_enabled,
            "listing_fee_kobo": config.listing_fee_kobo if config.listing_fee_enabled and config.paystack_enabled else 0,
            "ad_fee_required": config.ad_fee_enabled and config.paystack_enabled,
            "ad_fee_kobo": config.ad_fee_kobo if config.ad_fee_enabled and config.paystack_enabled else 0,
            "payments_enabled": config.paystack_enabled,
        },
    })

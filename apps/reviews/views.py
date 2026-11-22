from rest_framework import mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db.models import Avg, Count
from rest_framework.exceptions import ValidationError
from rest_framework.exceptions import PermissionDenied
from django.db.models import Q
from uuid import UUID
from apps.accounts.models import User, AnonymousMarketplaceIdentity
from apps.administration.models import PlatformSettings

from apps.accounts.permissions import IsVerifiedStudent
from apps.accounts.permissions import is_student_feature_eligible
from .models import ReviewReport, SellerReview
from .serializers import ReviewReportSerializer, SellerReviewSerializer


class SellerReviewViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = SellerReviewSerializer
    permission_classes = [IsVerifiedStudent]
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        if not PlatformSettings.get_solo().reviews_enabled:
            return SellerReview.objects.none()
        queryset = SellerReview.objects.select_related("reviewer", "seller", "anonymous_identity", "conversation", "job_engagement")
        identity_id = self.request.query_params.get("identity")
        if identity_id and identity_id.lower() in ("undefined", "null"):
            return SellerReview.objects.none()
        if identity_id:
            try:
                identity_id = str(UUID(identity_id))
            except (ValueError, TypeError, AttributeError):
                return SellerReview.objects.none()
        mine = self.request.query_params.get("mine") == "true"
        if mine:
            return queryset.filter(reviewer=self.request.user)
        visible_anonymous = Q(identity_type="anonymous", anonymous_identity__is_enabled=True)
        if self.request.user.is_authenticated:
            visible_anonymous |= Q(identity_type="anonymous", seller=self.request.user, anonymous_identity__user=self.request.user)
        queryset = queryset.filter(status=SellerReview.Status.APPROVED).filter(
            Q(identity_type="normal", seller__public_profile_enabled=True, seller__is_active=True, seller__is_suspended=False, seller__is_student_verified=True)
            | (visible_anonymous & Q(seller__is_active=True, seller__is_suspended=False, seller__is_student_verified=True))
        )
        if identity_id:
            normal_identity = User.objects.filter(public_id=identity_id, public_profile_enabled=True, is_active=True, is_suspended=False, is_student_verified=True).first()
            anonymous_identity = AnonymousMarketplaceIdentity.objects.select_related("user").filter(public_id=identity_id, user__is_active=True, user__is_suspended=False, user__is_student_verified=True).first()
            if anonymous_identity and not anonymous_identity.is_enabled and anonymous_identity.user_id != self.request.user.pk:
                anonymous_identity = None
            if not normal_identity and not anonymous_identity:
                return SellerReview.objects.none()
            queryset = queryset.filter(identity_public_id=identity_id)
        category = self.request.query_params.get("category", "").lower()
        if category in SellerReview.Category.values:
            queryset = queryset.filter(category=category)
        sentiment = self.request.query_params.get("sentiment", "").lower()
        if sentiment == "positive":
            queryset = queryset.filter(rating__gte=4)
        elif sentiment == "neutral":
            queryset = queryset.filter(rating=3)
        elif sentiment == "negative":
            queryset = queryset.filter(rating__lte=2)
        return queryset.order_by("-created_at", "-pk")

    @action(detail=False, methods=("get",), url_path="summary")
    def summary(self, request):
        if not PlatformSettings.get_solo().reviews_enabled:
            raise PermissionDenied("Reviews are currently disabled by the school.")
        identity_id = request.query_params.get("identity", "")
        try:
            identity_id = str(UUID(identity_id))
        except (ValueError, TypeError, AttributeError):
            raise ValidationError({"identity": "Choose an available public profile."})
        user = User.objects.filter(public_id=identity_id, public_profile_enabled=True, is_active=True, is_suspended=False).first()
        alias = AnonymousMarketplaceIdentity.objects.select_related("user").filter(public_id=identity_id, user__is_active=True, user__is_suspended=False).first()
        if alias and not alias.is_enabled and alias.user_id != request.user.pk:
            alias = None
        if (not user or not is_student_feature_eligible(user)) and (not alias or not is_student_feature_eligible(alias.user)):
            raise ValidationError({"identity": "Choose an available public profile."})
        if user and is_student_feature_eligible(user):
            rows = SellerReview.objects.filter(seller=user, identity_type="normal", identity_public_id=str(user.public_id), status=SellerReview.Status.APPROVED)
        else:
            rows = SellerReview.objects.filter(seller=alias.user, anonymous_identity=alias, identity_type="anonymous", identity_public_id=str(alias.public_id), status=SellerReview.Status.APPROVED)
        all_stats = rows.aggregate(average=Avg("rating"), count=Count("id"))
        category_stats = rows.values("category").annotate(average=Avg("rating"), count=Count("id"))
        by_category = {row["category"]: row for row in category_stats}
        def section(key):
            stats = by_category.get(key, {})
            return {"average": round(float(stats["average"]), 2) if stats.get("average") is not None else None, "count": stats.get("count", 0)}
        return Response({
            "identity": str(identity_id),
            "overall": {"average": round(float(all_stats["average"]), 2) if all_stats["average"] is not None else None, "count": all_stats["count"]},
            "marketplace": section(SellerReview.Category.MARKETPLACE),
            "jobs": section(SellerReview.Category.JOBS),
        })

    def perform_create(self, serializer):
        if not PlatformSettings.get_solo().reviews_enabled:
            raise PermissionDenied("Seller reviews are currently disabled by the school.")
        conversation = serializer.validated_data["conversation"]
        if SellerReview.objects.filter(conversation=conversation, reviewer=self.request.user, category=SellerReview.Category.MARKETPLACE).exists():
            raise ValidationError({"conversation": "You have already left feedback for this conversation."})
        serializer.save(status=SellerReview.Status.PENDING if PlatformSettings.get_solo().review_moderation_enabled else SellerReview.Status.APPROVED)


class ReviewReportViewSet(mixins.ListModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet):
    serializer_class = ReviewReportSerializer
    permission_classes = [IsVerifiedStudent]
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        return ReviewReport.objects.filter(reporter=self.request.user).select_related("review")

    def perform_create(self, serializer):
        report = serializer.save(reporter=self.request.user)
        from apps.accounts.models import User
        from apps.notifications.services import notify_user
        for admin in User.objects.filter(is_staff=True, is_active=True, is_suspended=False).iterator():
            notify_user(admin, "Review reported", f"A student reported review #{report.review_id} for {report.reason}.", {"kind": "review_report", "report_id": report.pk}, f"review-report:{report.pk}:admin:{admin.pk}")

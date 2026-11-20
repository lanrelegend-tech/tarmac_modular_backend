from rest_framework import mixins, permissions, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.exceptions import PermissionDenied
from apps.administration.models import PlatformSettings

from apps.accounts.permissions import IsVerifiedStudent
from .models import SellerReview
from .serializers import SellerReviewSerializer


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
        queryset = SellerReview.objects.select_related("reviewer", "seller", "order")
        seller_id = self.request.query_params.get("seller")
        mine = self.request.query_params.get("mine") == "true"
        if mine:
            return queryset.filter(reviewer=self.request.user)
        queryset = queryset.filter(status=SellerReview.Status.APPROVED)
        if seller_id:
            queryset = queryset.filter(seller_id=seller_id)
        return queryset

    def perform_create(self, serializer):
        if not PlatformSettings.get_solo().reviews_enabled:
            raise PermissionDenied("Seller reviews are currently disabled by the school.")
        order = serializer.validated_data["order"]
        if SellerReview.objects.filter(order=order).exists():
            raise ValidationError({"order": "You have already reviewed this completed order."})
        serializer.save(status=SellerReview.Status.PENDING if PlatformSettings.get_solo().review_moderation_enabled else SellerReview.Status.APPROVED)

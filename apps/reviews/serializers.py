from rest_framework import serializers

from apps.accounts.models import User
from apps.accounts.permissions import is_student_feature_eligible
from apps.orders.models import Order
from .models import SellerReview


class SellerReviewSerializer(serializers.ModelSerializer):
    reviewer_name = serializers.SerializerMethodField()
    seller_name = serializers.SerializerMethodField()
    order_id = serializers.IntegerField(read_only=True)
    order = serializers.PrimaryKeyRelatedField(queryset=Order.objects.all(), write_only=True)

    class Meta:
        model = SellerReview
        fields = (
            "id", "order", "order_id", "reviewer", "reviewer_name", "seller", "seller_name",
            "rating", "comment", "status", "created_at",
        )
        read_only_fields = (
            "id", "reviewer", "reviewer_name", "seller", "seller_name", "status", "created_at",
        )

    def get_reviewer_name(self, obj):
        return obj.reviewer.get_full_name().strip() or obj.reviewer.username

    def get_seller_name(self, obj):
        return obj.seller.get_full_name().strip() or obj.seller.username

    def validate(self, attrs):
        request = self.context["request"]
        order = attrs["order"]
        if order.buyer_id != request.user.pk:
            raise serializers.ValidationError({"order": "You can review only a purchase you made."})
        if order.status != Order.Status.COMPLETED:
            raise serializers.ValidationError({"order": "A review is available after the seller marks the sale complete."})
        if order.seller_id == request.user.pk:
            raise serializers.ValidationError({"order": "You cannot review your own seller account."})
        seller = User.objects.filter(pk=order.seller_id).first()
        if not is_student_feature_eligible(seller):
            raise serializers.ValidationError({"order": "This seller is not currently eligible for reviews."})
        return attrs

    def create(self, validated_data):
        order = validated_data.pop("order")
        return SellerReview.objects.create(
            order=order,
            reviewer=self.context["request"].user,
            seller=order.seller,
            **validated_data,
        )

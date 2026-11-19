from rest_framework import serializers
from .models import Order
class OrderSerializer(serializers.ModelSerializer):
    product_title = serializers.CharField(source="product.title", read_only=True)
    buyer_name = serializers.SerializerMethodField()
    seller_name = serializers.SerializerMethodField()

    class Meta: model=Order; fields="__all__"; read_only_fields=("buyer","seller","quantity","created_at","updated_at")

    def get_buyer_name(self, obj):
        return obj.buyer.get_full_name().strip() or obj.buyer.username

    def get_seller_name(self, obj):
        return obj.seller.get_full_name().strip() or obj.seller.username

    def validate(self, attrs):
        user = self.context["request"].user
        if self.instance is None:
            if attrs.get("status", Order.Status.REQUESTED) != Order.Status.REQUESTED:
                raise serializers.ValidationError({"status": "New orders must start as requested."})
            if attrs.get("agreed_price") is not None:
                raise serializers.ValidationError({"agreed_price": "The seller sets the agreed price after receiving the request."})
            return attrs

        order = self.instance
        if order.status in (Order.Status.COMPLETED, Order.Status.CANCELLED):
            raise serializers.ValidationError("Completed and cancelled orders cannot be changed.")
        is_buyer = user.pk == order.buyer_id
        allowed_fields = {"status", "buyer_note"} if is_buyer else {"status", "agreed_price"}
        unexpected = set(attrs) - allowed_fields
        if unexpected:
            raise serializers.ValidationError({field: "This field cannot be changed after an order is created." for field in unexpected})

        requested_status = attrs.get("status", order.status)
        if requested_status != order.status:
            if is_buyer:
                allowed_transition = order.status == Order.Status.REQUESTED and requested_status == Order.Status.CANCELLED
            else:
                allowed_transition = (
                    order.status == Order.Status.REQUESTED and requested_status in (Order.Status.ACCEPTED, Order.Status.CANCELLED)
                ) or (order.status == Order.Status.ACCEPTED and requested_status in (Order.Status.COMPLETED, Order.Status.CANCELLED))
            if not allowed_transition:
                raise serializers.ValidationError({"status": "This order status transition is not allowed."})
            if requested_status == Order.Status.ACCEPTED and order.product.status != "active":
                raise serializers.ValidationError({"status": "This listing is no longer available."})
        return attrs

from rest_framework import viewsets
from apps.accounts.permissions import IsVerifiedStudent, is_student_feature_eligible
from rest_framework.exceptions import ValidationError,PermissionDenied
from django.db import transaction
from django.utils import timezone
from apps.products.models import Product
from .models import Order
from apps.notifications.services import notify_user
from apps.administration.permissions import PlatformFeatureEnabled
from .serializers import OrderSerializer
class OrderViewSet(viewsets.ModelViewSet):
    platform_feature = "marketplace_enabled"
    serializer_class=OrderSerializer; permission_classes=[IsVerifiedStudent, PlatformFeatureEnabled]
    def get_queryset(self): return (Order.objects.select_related("buyer", "seller", "product").filter(buyer=self.request.user)|Order.objects.select_related("buyer", "seller", "product").filter(seller=self.request.user)).order_by("-created_at")
    def perform_create(self,serializer):
        product=serializer.validated_data["product"]
        if Order.objects.filter(buyer=self.request.user, product=product, status__in=(Order.Status.REQUESTED, Order.Status.ACCEPTED)).exists():
            raise ValidationError({"product": "You already have an open request for this listing."})
        if product.seller_id==self.request.user.id: raise ValidationError("You cannot order your own listing.")
        if not is_student_feature_eligible(self.request.user): raise PermissionDenied("An active student account is required.")
        if not is_student_feature_eligible(product.seller): raise ValidationError("This seller is not currently eligible to receive orders.")
        if not product.category.is_active or not product.category.is_allowed_for_sale: raise ValidationError("This listing category is no longer available.")
        if product.status != Product.Status.ACTIVE or (product.expires_at and product.expires_at <= timezone.now()): raise ValidationError("This listing is not available.")
        order = serializer.save(buyer=self.request.user,seller=product.seller)
        notify_user(
            product.seller,
            "New order request",
            f"{self.request.user.username} sent a request for your listing: {product.title}.",
            {"kind": "order_update", "order_id": order.pk, "product_id": product.pk, "title": product.title, "status": order.status},
            f"order-created:{order.pk}:seller",
        )
        notify_user(
            order.buyer,
            "Order request sent",
            f"Your request for {product.title} was sent to the seller. We’ll email you when they respond.",
            {"kind": "order_update", "order_id": order.pk, "product_id": product.pk, "title": product.title, "status": order.status},
            f"order-created:{order.pk}:buyer",
        )
    @transaction.atomic
    def perform_update(self,serializer):
        original = serializer.instance
        if self.request.user.id not in (original.seller_id, original.buyer_id):
            raise PermissionDenied("Only the buyer or seller can update this order.")
        order = Order.objects.select_for_update().get(pk=original.pk)
        if order.status != original.status or order.updated_at != original.updated_at:
            raise ValidationError({"status": "This order changed while you were updating it. Reload and try again."})
        serializer.instance = order
        old_status = order.status
        product = Product.objects.select_for_update().get(pk=order.product_id)
        if serializer.validated_data.get("status") == Order.Status.ACCEPTED and (
            product.status != Product.Status.ACTIVE or (product.expires_at and product.expires_at <= timezone.now())
        ):
            raise ValidationError({"status": "This listing is no longer available."})
        if serializer.validated_data.get("status") == Order.Status.ACCEPTED and "agreed_price" not in serializer.validated_data:
            serializer.validated_data["agreed_price"] = product.price
        updated = serializer.save()
        if old_status != updated.status and updated.status == Order.Status.ACCEPTED:
            product.status = Product.Status.RESERVED
            product.save(update_fields=("status", "updated_at"))
        elif old_status != updated.status and updated.status == Order.Status.COMPLETED:
            product.status = Product.Status.SOLD
            product.save(update_fields=("status", "updated_at"))
        elif old_status == Order.Status.ACCEPTED and updated.status == Order.Status.CANCELLED:
            product.status = Product.Status.EXPIRED if product.expires_at and product.expires_at <= timezone.now() else Product.Status.ACTIVE
            product.save(update_fields=("status", "updated_at"))
        if old_status != updated.status:
            recipient = updated.buyer if self.request.user.id == updated.seller_id else updated.seller
            labels = {
                Order.Status.ACCEPTED: "The seller accepted your request.",
                Order.Status.CANCELLED: "The other student cancelled the request.",
                Order.Status.COMPLETED: "The seller marked the sale complete.",
            }
            notify_user(recipient, "Order updated", labels.get(updated.status, "Your order was updated."),
                        {"kind": "order_update", "order_id": updated.pk, "product_id": product.pk, "title": product.title, "status": updated.status}, f"order-status:{updated.pk}:{updated.status}:{recipient.pk}")
        elif old_status == updated.status and "agreed_price" in serializer.validated_data:
            notify_user(updated.buyer, "Seller proposed a price", "The seller updated the proposed price for your order.",
                        {"kind": "order_update", "order_id": updated.pk, "product_id": product.pk, "title": product.title, "status": updated.status}, f"order-price:{updated.pk}:{updated.agreed_price}")

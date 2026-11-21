import mimetypes
from datetime import timedelta
from django.db.models import F, Q
from django.db.models import Count
from rest_framework.exceptions import ValidationError
from django.http import FileResponse
from django.utils import timezone
from rest_framework import viewsets, permissions
from apps.accounts.permissions import IsVerifiedStudent
from apps.administration.models import PlatformSettings, AdminLog
from apps.administration.permissions import PlatformFeatureEnabled
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from .models import Category, Product, ProductImage, Favourite
from .serializers import CategorySerializer, ProductSerializer, ProductImageUploadSerializer, FavouriteSerializer
from .permissions import VerifiedStudentOrReadOnly

class CategoryViewSet(viewsets.ModelViewSet):
    platform_feature = "marketplace_enabled"
    queryset=Category.objects.filter(is_active=True)
    serializer_class=CategorySerializer
    def get_permissions(self):
        if self.request.user and self.request.user.is_staff:
            return [permissions.IsAdminUser()]
        return [IsVerifiedStudent(), PlatformFeatureEnabled()]

    def get_queryset(self):
        if self.request.user and self.request.user.is_staff:
            return Category.objects.annotate(products_count=Count("products"))
        return Category.objects.filter(is_active=True, is_allowed_for_sale=True).annotate(products_count=Count("products"))

    def perform_create(self, serializer):
        category = serializer.save()
        AdminLog.objects.create(actor=self.request.user, action="category_created", target_type="Category", target_id=str(category.pk), metadata={"name": category.name})

    def perform_update(self, serializer):
        category = serializer.save()
        AdminLog.objects.create(actor=self.request.user, action="category_updated", target_type="Category", target_id=str(category.pk), metadata={"name": category.name, "is_active": category.is_active, "is_allowed_for_sale": category.is_allowed_for_sale})

    def perform_destroy(self, instance):
        if instance.products.exists():
            raise ValidationError({"detail": "This category has listings. Move those listings to another category before deleting it."})
        category_id, category_name = instance.pk, instance.name
        instance.delete()
        AdminLog.objects.create(actor=self.request.user, action="category_deleted", target_type="Category", target_id=str(category_id), metadata={"name": category_name})

class ProductViewSet(viewsets.ModelViewSet):
    platform_feature = "marketplace_enabled"
    serializer_class=ProductSerializer; permission_classes=[VerifiedStudentOrReadOnly, PlatformFeatureEnabled]; filterset_fields=["category","condition","status"]; search_fields=["title","description","location"]; ordering_fields=["price","created_at"]
    def get_queryset(self):
        qs=Product.objects.select_related("seller","seller__course","category","anonymous_identity").prefetch_related("images")
        platform = PlatformSettings.get_solo()
        seller_access = Q()
        if platform.email_verification_required:
            seller_access &= Q(seller__email_verified=True)
        if platform.student_verification_enabled:
            seller_access &= Q(seller__is_student_verified=True)
        valid_identity = Q(identity_type=Product.IdentityType.NORMAL) | Q(anonymous_identity__is_enabled=True)
        available = qs.filter(status=Product.Status.ACTIVE, visibility=Product.Visibility.PUBLIC, seller__is_active=True, seller__is_suspended=False, category__is_active=True, category__is_allowed_for_sale=True).filter(valid_identity, seller_access).filter(Q(expires_at__isnull=True)|Q(expires_at__gt=timezone.now()))
        invite_only = qs.filter(status=Product.Status.ACTIVE, visibility=Product.Visibility.INVITE_ONLY, audience_grants__user=self.request.user, seller__is_active=True, seller__is_suspended=False, category__is_active=True, category__is_allowed_for_sale=True).filter(valid_identity, seller_access).filter(Q(expires_at__isnull=True)|Q(expires_at__gt=timezone.now()))
        if self.action == "list" and self.request.query_params.get("sponsored") == "true":
            if not platform.sponsored_products_enabled or not platform.featured_products_enabled:
                return Product.objects.none()
            available = available.filter(is_featured=True).filter(Q(featured_until__isnull=True) | Q(featured_until__gt=timezone.now()))
            return available.order_by(F("featured_position").asc(nulls_last=True), "-created_at").distinct()
        if self.action == "list" and self.request.query_params.get("featured") == "true":
            if not platform.featured_products_enabled:
                return Product.objects.none()
            available = available.filter(is_featured=True).filter(Q(featured_until__isnull=True) | Q(featured_until__gt=timezone.now()))
            return available.order_by(F("featured_position").asc(nulls_last=True), "-created_at").distinct()
        if self.action in ("list","retrieve"): return (available | invite_only | qs.filter(seller=self.request.user)).distinct()
        return qs.filter(seller=self.request.user)
    def perform_create(self, serializer):
        platform = PlatformSettings.get_solo()
        visibility = serializer.validated_data.get("visibility", Product.Visibility.PUBLIC)
        sponsor_requested = serializer.validated_data.pop("sponsor_listing", False)
        listing_payment = visibility != Product.Visibility.DRAFT and platform.listing_fee_enabled and platform.paystack_enabled and platform.listing_fee_kobo > 0
        sponsor_payment = visibility != Product.Visibility.DRAFT and sponsor_requested and platform.sponsored_product_fee_enabled and platform.paystack_enabled and platform.sponsored_product_fee_kobo > 0
        status_value = Product.Status.ACTIVE if visibility == Product.Visibility.DRAFT else (Product.Status.PENDING_PAYMENT if listing_payment or sponsor_payment else (Product.Status.PENDING_REVIEW if platform.listing_approval_required else Product.Status.ACTIVE))
        values = {"seller": self.request.user, "status": status_value,
                  "sponsor_requested": sponsor_requested,
                  "sponsorship_fee_kobo": platform.sponsored_product_fee_kobo if sponsor_payment else 0,
                  "sponsorship_duration_days": platform.sponsored_product_duration_days}
        if status_value == Product.Status.ACTIVE and visibility != Product.Visibility.DRAFT:
            values["expires_at"] = timezone.now() + timedelta(days=30)
            if sponsor_requested:
                values.update(is_featured=True, featured_until=timezone.now() + timedelta(days=platform.sponsored_product_duration_days))
        if visibility == Product.Visibility.DRAFT:
            values["sponsor_requested"] = False
        serializer.save(**values)
    def perform_update(self, serializer):
        if serializer.instance.seller_id != self.request.user.id:
            raise PermissionDenied("You can only edit your own listing.")
        platform = PlatformSettings.get_solo()
        status_value = Product.Status.PENDING_PAYMENT if serializer.instance.status == Product.Status.PENDING_PAYMENT and platform.listing_fee_enabled and platform.paystack_enabled else Product.Status.PENDING_REVIEW
        serializer.save(status=status_value, expires_at=None)

    @action(detail=True, methods=["post"], url_path="mark-sold")
    def mark_sold(self, request, pk=None):
        product = self.get_object()
        if product.seller_id != request.user.pk:
            raise PermissionDenied("Only the listing owner can mark it as sold.")
        if product.status not in (Product.Status.ACTIVE, Product.Status.RESERVED):
            raise ValidationError({"status": "Only an active listing can be marked as sold."})
        product.status = Product.Status.SOLD
        product.save(update_fields=("status", "updated_at"))
        return Response(self.get_serializer(product).data)

class ProductImageViewSet(viewsets.ModelViewSet):
    platform_feature = "marketplace_enabled"
    serializer_class=ProductImageUploadSerializer
    permission_classes=[VerifiedStudentOrReadOnly, PlatformFeatureEnabled]
    http_method_names=["get", "post", "patch", "delete", "head", "options"]
    def get_queryset(self):
        platform = PlatformSettings.get_solo()
        seller_access = Q()
        if platform.email_verification_required:
            seller_access &= Q(product__seller__email_verified=True)
        if platform.student_verification_enabled:
            seller_access &= Q(product__seller__is_student_verified=True)
        available = ProductImage.objects.filter(product__status=Product.Status.ACTIVE, product__visibility=Product.Visibility.PUBLIC, product__seller__is_active=True, product__seller__is_suspended=False, product__category__is_active=True, product__category__is_allowed_for_sale=True).filter(seller_access).filter(Q(product__identity_type=Product.IdentityType.NORMAL)|Q(product__anonymous_identity__is_enabled=True)).filter(Q(product__expires_at__isnull=True)|Q(product__expires_at__gt=timezone.now()))
        invited = ProductImage.objects.filter(product__status=Product.Status.ACTIVE, product__visibility=Product.Visibility.INVITE_ONLY, product__audience_grants__user=self.request.user, product__seller__is_active=True, product__seller__is_suspended=False).filter(seller_access).filter(Q(product__identity_type=Product.IdentityType.NORMAL)|Q(product__anonymous_identity__is_enabled=True)).filter(Q(product__expires_at__isnull=True)|Q(product__expires_at__gt=timezone.now()))
        return available | invited | ProductImage.objects.filter(product__seller=self.request.user)
    @action(detail=True, methods=["get"], url_path="file")
    def file(self, request, pk=None):
        item = self.get_object()
        content_type = mimetypes.guess_type(item.image.name)[0] or "application/octet-stream"
        return FileResponse(item.image.open("rb"), content_type=content_type)
    def perform_create(self, serializer):
        product = serializer.validated_data["product"]
        if product.seller_id != self.request.user.id:
            raise PermissionDenied("You can only upload images to your own listing.")
        serializer.save()
    def perform_update(self, serializer):
        if serializer.instance.product.seller_id != self.request.user.id:
            raise PermissionDenied("You can only update images on your own listing.")
        serializer.save()
    def perform_destroy(self, instance):
        if instance.product.seller_id != self.request.user.id:
            raise PermissionDenied("You can only delete images from your own listing.")
        instance.delete()

class FavouriteViewSet(viewsets.ModelViewSet):
    platform_feature = "marketplace_enabled"
    serializer_class=FavouriteSerializer; permission_classes=[IsVerifiedStudent, PlatformFeatureEnabled]
    def get_queryset(self): return Favourite.objects.filter(user=self.request.user)
    def perform_create(self, serializer): serializer.save(user=self.request.user)

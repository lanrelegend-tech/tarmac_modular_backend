import mimetypes
from django.db.models import Q
from django.http import FileResponse
from django.utils import timezone
from rest_framework import viewsets
from apps.accounts.permissions import IsVerifiedStudent
from apps.administration.models import PlatformSettings
from apps.administration.permissions import PlatformFeatureEnabled
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from .models import Category, Product, ProductImage, Favourite
from .serializers import CategorySerializer, ProductSerializer, ProductImageUploadSerializer, FavouriteSerializer
from .permissions import VerifiedStudentOrReadOnly

class CategoryViewSet(viewsets.ReadOnlyModelViewSet):
    platform_feature = "marketplace_enabled"
    queryset=Category.objects.filter(is_active=True)
    serializer_class=CategorySerializer
    permission_classes=[IsVerifiedStudent, PlatformFeatureEnabled]

class ProductViewSet(viewsets.ModelViewSet):
    platform_feature = "marketplace_enabled"
    serializer_class=ProductSerializer; permission_classes=[VerifiedStudentOrReadOnly, PlatformFeatureEnabled]; filterset_fields=["category","condition","status"]; search_fields=["title","description","location"]; ordering_fields=["price","created_at"]
    def get_queryset(self):
        qs=Product.objects.select_related("seller","category").prefetch_related("images")
        available = qs.filter(status=Product.Status.ACTIVE, seller__is_active=True, seller__is_suspended=False, seller__is_student_verified=True, category__is_active=True, category__is_allowed_for_sale=True).filter(Q(expires_at__isnull=True)|Q(expires_at__gt=timezone.now()))
        if self.action in ("list","retrieve"): return (available | qs.filter(seller=self.request.user)).distinct()
        return qs.filter(seller=self.request.user)
    def perform_create(self, serializer):
        platform = PlatformSettings.get_solo()
        status_value = Product.Status.PENDING_PAYMENT if platform.listing_fee_enabled and platform.paystack_enabled else Product.Status.PENDING_REVIEW
        serializer.save(seller=self.request.user, status=status_value)
    def perform_update(self, serializer):
        if serializer.instance.seller_id != self.request.user.id:
            raise PermissionDenied("You can only edit your own listing.")
        platform = PlatformSettings.get_solo()
        status_value = Product.Status.PENDING_PAYMENT if serializer.instance.status == Product.Status.PENDING_PAYMENT and platform.listing_fee_enabled and platform.paystack_enabled else Product.Status.PENDING_REVIEW
        serializer.save(status=status_value, expires_at=None)

class ProductImageViewSet(viewsets.ModelViewSet):
    platform_feature = "marketplace_enabled"
    serializer_class=ProductImageUploadSerializer
    permission_classes=[VerifiedStudentOrReadOnly, PlatformFeatureEnabled]
    http_method_names=["get", "post", "patch", "delete", "head", "options"]
    def get_queryset(self):
        available = ProductImage.objects.filter(product__status=Product.Status.ACTIVE, product__seller__is_active=True, product__seller__is_suspended=False, product__seller__is_student_verified=True, product__category__is_active=True, product__category__is_allowed_for_sale=True).filter(Q(product__expires_at__isnull=True)|Q(product__expires_at__gt=timezone.now()))
        return available | ProductImage.objects.filter(product__seller=self.request.user)
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

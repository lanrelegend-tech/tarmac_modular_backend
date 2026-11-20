from rest_framework import serializers
from .models import Category, Product, ProductImage, Favourite
class CategorySerializer(serializers.ModelSerializer):
    class Meta: model=Category; fields="__all__"
class ProductImageSerializer(serializers.ModelSerializer):
    image = serializers.SerializerMethodField()
    class Meta: model=ProductImage; fields=("id","image","alt_text")
    def get_image(self, obj):
        request = self.context.get("request")
        path = f"/api/products/images/{obj.pk}/file/"
        return request.build_absolute_uri(path) if request else path

class ProductImageUploadSerializer(serializers.ModelSerializer):
    image = serializers.ImageField(write_only=True)
    image_url = serializers.SerializerMethodField()
    class Meta:
        model = ProductImage
        fields = ("id", "product", "image", "image_url", "alt_text")
        read_only_fields = ("id",)

    def get_image_url(self, obj):
        request = self.context.get("request")
        path = f"/api/products/images/{obj.pk}/file/"
        return request.build_absolute_uri(path) if request else path

    def validate_image(self, image):
        if image.size > 5 * 1024 * 1024:
            raise serializers.ValidationError("Image files must be 5 MB or smaller.")
        return image

    def validate(self, attrs):
        if self.instance and attrs.get("product", self.instance.product) != self.instance.product:
            raise serializers.ValidationError({"product": "An image cannot be moved to another listing."})
        if self.instance is None:
            from apps.administration.models import PlatformSettings
            if attrs["product"].images.count() >= PlatformSettings.get_solo().max_product_images:
                raise serializers.ValidationError({"product": "This listing has reached the image limit set by the school."})
        return attrs
class ProductSerializer(serializers.ModelSerializer):
    is_featured = serializers.SerializerMethodField()
    images = ProductImageSerializer(many=True, read_only=True)
    seller_id = serializers.IntegerField(source="seller.id", read_only=True)
    seller_name = serializers.SerializerMethodField()
    seller_department = serializers.CharField(source="seller.department", read_only=True)
    seller_level = serializers.CharField(source="seller.level", read_only=True)
    seller_joined = serializers.DateTimeField(source="seller.date_joined", read_only=True)
    category_name = serializers.CharField(source="category.name", read_only=True)

    class Meta:
        model = Product
        fields = ("id", "seller_id", "seller_name", "seller_department", "seller_level", "seller_joined", "category", "category_name", "title", "description", "condition", "price", "negotiable", "location", "status", "expires_at", "is_featured", "featured_until", "images", "created_at", "updated_at")
        read_only_fields = ("status", "expires_at", "is_featured", "featured_until", "created_at", "updated_at")

    def get_seller_name(self, obj):
        return obj.seller.get_full_name().strip() or obj.seller.username
    def get_is_featured(self, obj):
        from django.utils import timezone
        from apps.administration.models import PlatformSettings
        return bool(PlatformSettings.get_solo().featured_products_enabled and obj.is_featured and (obj.featured_until is None or obj.featured_until > timezone.now()))
    def validate_category(self, category):
        if not category.is_active or not category.is_allowed_for_sale:
            raise serializers.ValidationError("This category is not currently available for listings.")
        return category
class FavouriteSerializer(serializers.ModelSerializer):
    class Meta: model=Favourite; fields=("id","product","created_at"); read_only_fields=("created_at",)

    def validate_product(self, product):
        request = self.context.get("request")
        if product.status != Product.Status.ACTIVE or product.seller_id == request.user.id or not product.category.is_active or not product.category.is_allowed_for_sale:
            raise serializers.ValidationError("You can favourite another student's active listing only.")
        return product

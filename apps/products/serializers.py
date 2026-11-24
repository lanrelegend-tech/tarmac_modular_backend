from rest_framework import serializers
from apps.administration.models import PlatformSettings
from apps.administration.campuses import validate_campus_choice, validate_area_choice
from .models import Category, Product, ProductImage, Favourite, ProductAudienceGrant
class CategorySerializer(serializers.ModelSerializer):
    products_count = serializers.IntegerField(read_only=True, default=0)
    class Meta: model=Category; fields="__all__"

    def validate_subcategories(self, value):
        if not isinstance(value, list) or len(value) > 50:
            raise serializers.ValidationError("Provide a list of no more than 50 subcategories.")
        clean = []
        seen = set()
        for item in value:
            if not isinstance(item, str) or not item.strip() or len(item.strip()) > 80:
                raise serializers.ValidationError("Each subcategory must be text up to 80 characters.")
            label = item.strip()
            if label.casefold() not in seen:
                clean.append(label)
                seen.add(label.casefold())
        return clean
class ProductImageSerializer(serializers.ModelSerializer):
    image = serializers.SerializerMethodField()
    class Meta: model=ProductImage; fields=("id","image","alt_text")
    def get_image(self, obj):
        request = self.context.get("request")
        path = f"/api/products/images/{obj.pk}/file/"
        return request.build_absolute_uri(path) if request else path

class ProductOwnerListSerializer(serializers.ModelSerializer):
    primary_image = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = ("id", "public_id", "title", "price", "status", "primary_image", "show_on_profile", "identity_type")

    def get_primary_image(self, obj):
        images = list(obj.images.all())
        if not images:
            return ""
        request = self.context.get("request")
        path = f"/api/products/images/{images[0].pk}/file/"
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
    campus = serializers.CharField(required=True, max_length=120)
    area = serializers.CharField(required=False, allow_blank=True, max_length=120)
    is_featured = serializers.SerializerMethodField()
    sponsor_listing = serializers.BooleanField(write_only=True, required=False, default=False)
    images = ProductImageSerializer(many=True, read_only=True)
    seller_public_id = serializers.SerializerMethodField()
    seller_identity_type = serializers.CharField(source="identity_type", read_only=True)
    seller_profile_visible = serializers.SerializerMethodField()
    is_owner = serializers.SerializerMethodField()
    seller_verified = serializers.BooleanField(source="seller.is_student_verified", read_only=True)
    seller_name = serializers.SerializerMethodField()
    seller_campus = serializers.CharField(source="seller.campus", read_only=True)
    seller_department = serializers.SerializerMethodField()
    seller_level = serializers.SerializerMethodField()
    seller_status = serializers.SerializerMethodField()
    seller_course = serializers.SerializerMethodField()
    category_name = serializers.CharField(source="category.name", read_only=True)
    audience_public_ids = serializers.ListField(child=serializers.UUIDField(), write_only=True, required=False)

    class Meta:
        model = Product
        fields = ("id", "public_id", "seller_public_id", "seller_identity_type", "seller_profile_visible", "is_owner", "seller_verified", "seller_name", "seller_campus", "campus", "area", "seller_department", "seller_level", "seller_course", "seller_status", "category", "category_name", "title", "description", "condition", "price", "negotiable", "location", "visibility", "identity_type", "show_on_profile", "audience_public_ids", "status", "expires_at", "is_featured", "featured_until", "sponsor_listing", "sponsor_requested", "sponsorship_fee_kobo", "sponsorship_duration_days", "featured_position", "images", "created_at", "updated_at")
        read_only_fields = ("status", "expires_at", "is_featured", "featured_until", "created_at", "updated_at")

    def get_seller_name(self, obj):
        if obj.identity_type == Product.IdentityType.ANONYMOUS:
            return obj.anonymous_identity.handle if obj.anonymous_identity_id else "Anonymous account"
        if not obj.seller.public_name_enabled:
            return obj.seller.username
        return obj.seller.get_full_name().strip() or obj.seller.username
    def get_seller_public_id(self, obj):
        if obj.identity_type == Product.IdentityType.ANONYMOUS:
            return str(obj.anonymous_identity.public_id) if obj.anonymous_identity_id else None
        return str(obj.seller.public_id)
    def get_seller_profile_visible(self, obj):
        if obj.identity_type == Product.IdentityType.ANONYMOUS:
            return bool(obj.anonymous_identity_id and obj.anonymous_identity.is_enabled)
        return obj.seller.public_profile_enabled
    def get_is_owner(self, obj):
        request = self.context.get("request")
        return bool(request and request.user.is_authenticated and obj.seller_id == request.user.pk)
    def get_seller_department(self, obj):
        return obj.seller.department if obj.identity_type == Product.IdentityType.NORMAL and obj.seller.public_department_enabled else ""
    def get_seller_level(self, obj):
        return obj.seller.level if obj.identity_type == Product.IdentityType.NORMAL and obj.seller.public_level_enabled else ""
    def get_seller_course(self, obj):
        if obj.identity_type != Product.IdentityType.NORMAL or not obj.seller.public_profile_enabled or not obj.seller.public_level_enabled:
            return None
        return obj.seller.course.name if obj.seller.course_id else None
    def get_seller_status(self, obj):
        return obj.seller.student_status if obj.seller.is_student_verified else "pending"
    def validate_campus(self, value):
        return validate_campus_choice(value)
    def get_is_featured(self, obj):
        from django.utils import timezone
        from apps.administration.models import PlatformSettings
        platform = self.context.get("_tarmac_product_platform_settings")
        if platform is None:
            platform = PlatformSettings.get_solo()
            self.context["_tarmac_product_platform_settings"] = platform
        return bool(platform.featured_products_enabled and platform.sponsored_products_enabled and obj.is_featured and (obj.featured_until is None or obj.featured_until > timezone.now()))
    def validate_category(self, category):
        if not category.is_active or not category.is_allowed_for_sale:
            raise serializers.ValidationError("This category is not currently available for listings.")
        return category

    def validate_sponsor_listing(self, value):
        if value and not PlatformSettings.get_solo().sponsored_products_enabled:
            raise serializers.ValidationError("Product sponsorship is currently unavailable.")
        if value and self.instance:
            raise serializers.ValidationError("Sponsorship can only be selected when creating a listing.")
        return value

    def get_fields(self):
        fields = super().get_fields()
        for name in ("sponsor_requested", "sponsorship_fee_kobo", "sponsorship_duration_days", "featured_position"):
            fields[name].read_only = True
        return fields

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if "area" in attrs or self.instance is None:
            campus = attrs.get("campus", getattr(self.instance, "campus", ""))
            area = attrs.get("area", getattr(self.instance, "area", ""))
            attrs["area"] = validate_area_choice(campus, area)
        identity_type = attrs.get("identity_type", getattr(self.instance, "identity_type", Product.IdentityType.NORMAL))
        visibility = attrs.get("visibility", getattr(self.instance, "visibility", Product.Visibility.PUBLIC))
        audience_public_ids = attrs.get("audience_public_ids", None)
        audience_users = None
        if identity_type == Product.IdentityType.ANONYMOUS:
            user = self.context["request"].user
            identity = getattr(user, "anonymous_marketplace_identity", None)
            if not identity or not identity.is_enabled:
                raise serializers.ValidationError({"identity_type": "Create or enable an anonymous marketplace identity first."})
            attrs["anonymous_identity"] = identity
        else:
            attrs["anonymous_identity"] = None
        if visibility == Product.Visibility.INVITE_ONLY:
            if audience_public_ids is None and self.instance is None:
                raise serializers.ValidationError({"audience_public_ids": "Choose at least one verified student for an invite-only listing."})
            if audience_public_ids is not None:
                from apps.accounts.models import User
                from apps.accounts.permissions import is_student_feature_eligible
                if not audience_public_ids or len(audience_public_ids) > 100 or len(set(audience_public_ids)) != len(audience_public_ids):
                    raise serializers.ValidationError({"audience_public_ids": "Choose between 1 and 100 different students."})
                users = list(User.objects.filter(public_id__in=audience_public_ids, is_active=True, is_suspended=False))
                if len(users) != len(audience_public_ids) or any(not user.is_student_verified or not is_student_feature_eligible(user) for user in users):
                    raise serializers.ValidationError({"audience_public_ids": "Invite only active, verified students."})
                if self.context["request"].user.pk in {user.pk for user in users}:
                    raise serializers.ValidationError({"audience_public_ids": "You do not need to invite yourself."})
                audience_users = users
        elif audience_public_ids:
            raise serializers.ValidationError({"audience_public_ids": "Audience invites are only used for invite-only listings."})
        attrs.pop("audience_public_ids", None)
        attrs["_audience_users"] = audience_users
        return attrs

    def create(self, validated_data):
        audience = validated_data.pop("_audience_users", None) or []
        product = super().create(validated_data)
        ProductAudienceGrant.objects.bulk_create([ProductAudienceGrant(product=product, user=user) for user in audience])
        return product

    def update(self, instance, validated_data):
        audience = validated_data.pop("_audience_users", None)
        product = super().update(instance, validated_data)
        if audience is not None:
            ids = {user.pk for user in audience}
            ProductAudienceGrant.objects.filter(product=product).exclude(user_id__in=ids).delete()
            existing = set(ProductAudienceGrant.objects.filter(product=product).values_list("user_id", flat=True))
            ProductAudienceGrant.objects.bulk_create([ProductAudienceGrant(product=product, user=user) for user in audience if user.pk not in existing])
        return product
class FavouriteSerializer(serializers.ModelSerializer):
    class Meta: model=Favourite; fields=("id","product","created_at"); read_only_fields=("created_at",)

    def create(self, validated_data):
        # Retries and quick duplicate taps should return the saved row instead
        # of violating the unique (user, product) constraint.
        favourite, _ = Favourite.objects.get_or_create(**validated_data)
        return favourite

    def validate_product(self, product):
        request = self.context.get("request")
        can_view = product.visibility == Product.Visibility.PUBLIC or (product.visibility == Product.Visibility.INVITE_ONLY and product.audience_grants.filter(user=request.user).exists())
        identity_available = product.identity_type == Product.IdentityType.NORMAL or (product.anonymous_identity_id and product.anonymous_identity.is_enabled)
        if product.status != Product.Status.ACTIVE or not can_view or not identity_available or product.seller_id == request.user.id or not product.category.is_active or not product.category.is_allowed_for_sale:
            raise serializers.ValidationError("You can favourite another student's active listing only.")
        return product

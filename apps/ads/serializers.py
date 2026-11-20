from django.conf import settings
from rest_framework import serializers

from .models import AdCampaign
from apps.accounts.models import User


class AdCampaignSerializer(serializers.ModelSerializer):
    image = serializers.ImageField(write_only=True, required=False, allow_null=True)
    image_url = serializers.SerializerMethodField()
    advertiser_email = serializers.EmailField(write_only=True, required=False)
    owner_email = serializers.EmailField(source="advertiser.email", read_only=True)
    advertiser_name = serializers.SerializerMethodField()
    package_name = serializers.CharField(source="package.name", read_only=True, allow_null=True)
    admin_amount_kobo = serializers.IntegerField(write_only=True, required=False, min_value=0)

    class Meta:
        model = AdCampaign
        fields = ("id", "package", "package_name", "business_name", "advertiser_email", "owner_email", "advertiser_name", "pages", "display_image", "admin_amount_kobo", "price_kobo", "title", "description", "image", "image_url", "target_url", "placement", "status", "click_count", "impression_count", "starts_at", "ends_at", "created_at")
        read_only_fields = ("id", "status", "price_kobo", "click_count", "impression_count", "created_at")

    def get_fields(self):
        fields = super().get_fields()
        request = self.context.get("request")
        if not (request and request.user.is_staff):
            fields["advertiser_email"].read_only = True
            fields["admin_amount_kobo"].read_only = True
        return fields

    def get_image_url(self, obj):
        if not obj.image:
            return None
        request = self.context.get("request")
        path = f"/api/ads/{obj.pk}/image/"
        return request.build_absolute_uri(path) if request else path

    def get_advertiser_name(self, obj): return obj.advertiser.get_full_name().strip() or obj.advertiser.username

    def validate_image(self, image):
        if image.size > 5 * 1024 * 1024:
            raise serializers.ValidationError("Ad images must be 5 MB or smaller.")
        return image

    def validate_target_url(self, value):
        if value and not settings.DEBUG and not value.lower().startswith("https://"):
            raise serializers.ValidationError("Ad destination URLs must use HTTPS.")
        return value

    def validate(self, attrs):
        request = self.context.get("request")
        is_admin = bool(request and request.user.is_staff)
        if not is_admin and self.instance is None and not attrs.get("image"):
            raise serializers.ValidationError({"image": "Upload an image for your advertising campaign."})
        if is_admin and self.instance is None and not attrs.get("advertiser_email"):
            attrs["advertiser"] = request.user
        email = attrs.get("advertiser_email")
        if email:
            advertiser = User.objects.filter(email__iexact=email, is_active=True, is_suspended=False, is_student_verified=True).first()
            if advertiser is None:
                raise serializers.ValidationError({"advertiser_email": "Choose an active verified student account."})
            attrs["advertiser"] = advertiser
            attrs.pop("advertiser_email", None)
        if "admin_amount_kobo" in attrs:
            if not is_admin: raise serializers.ValidationError({"admin_amount_kobo": "Only administrators can set campaign amounts."})
            attrs["price_kobo"] = attrs.pop("admin_amount_kobo")
        package = attrs.get("package", getattr(self.instance, "package", None))
        if package and not package.is_active:
            raise serializers.ValidationError({"package": "Choose an active advertising package."})
        starts = attrs.get("starts_at", getattr(self.instance, "starts_at", None))
        ends = attrs.get("ends_at", getattr(self.instance, "ends_at", None))
        if starts and ends and ends <= starts:
            raise serializers.ValidationError({"ends_at": "End time must be after start time."})
        return attrs

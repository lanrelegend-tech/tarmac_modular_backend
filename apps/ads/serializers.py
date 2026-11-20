from django.conf import settings
from rest_framework import serializers

from .models import AdCampaign
from apps.accounts.models import User


class AdCampaignSerializer(serializers.ModelSerializer):
    PAGE_CHOICES = {"homepage", "marketplace", "lost_found", "announcements"}
    image = serializers.ImageField(write_only=True, required=False, allow_null=True)
    image_url = serializers.SerializerMethodField()
    advertiser_email = serializers.EmailField(write_only=True, required=False)
    owner_email = serializers.EmailField(source="advertiser.email", read_only=True)
    advertiser_name = serializers.SerializerMethodField()
    package_name = serializers.CharField(source="package.name", read_only=True, allow_null=True)
    admin_amount_kobo = serializers.IntegerField(write_only=True, required=False, min_value=0)

    class Meta:
        model = AdCampaign
        fields = ("id", "package", "package_name", "business_name", "advertiser_email", "owner_email", "advertiser_name", "pages", "display_image", "admin_amount_kobo", "price_kobo", "duration_days", "title", "description", "image", "image_url", "target_url", "placement", "status", "click_count", "impression_count", "starts_at", "ends_at", "created_at")
        read_only_fields = ("id", "status", "price_kobo", "duration_days", "click_count", "impression_count", "created_at")

    def get_fields(self):
        fields = super().get_fields()
        request = self.context.get("request")
        if not (request and request.user.is_staff):
            fields.pop("owner_email", None)
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
            advertiser = User.objects.filter(email__iexact=email, is_active=True, is_suspended=False).first()
            from apps.accounts.permissions import is_student_feature_eligible
            if not is_student_feature_eligible(advertiser):
                raise serializers.ValidationError({"advertiser_email": "Choose an active verified student account."})
            attrs["advertiser"] = advertiser
            attrs.pop("advertiser_email", None)
        if "admin_amount_kobo" in attrs:
            if not is_admin: raise serializers.ValidationError({"admin_amount_kobo": "Only administrators can set campaign amounts."})
            attrs["price_kobo"] = attrs.pop("admin_amount_kobo")
        package = attrs.get("package", getattr(self.instance, "package", None))
        if self.instance and not is_admin:
            if "package" in attrs and getattr(attrs["package"], "pk", None) != self.instance.package_id:
                raise serializers.ValidationError({"package": "An ad plan cannot be changed after campaign submission."})
            if "pages" in attrs and attrs["pages"] != self.instance.pages:
                raise serializers.ValidationError({"pages": "Ad placements cannot be changed after campaign submission."})
        if package and not package.is_active:
            raise serializers.ValidationError({"package": "Choose an active advertising package."})
        existing_pages = getattr(self.instance, "pages", None)
        pages = attrs.get("pages", existing_pages)
        if pages is None:
            pages = list(package.allowed_pages[:package.max_pages]) if package else (
                ["lost_found"] if attrs.get("placement", AdCampaign.Placement.MARKETPLACE) == AdCampaign.Placement.LOST_FOUND else ["marketplace"]
            )
        if not isinstance(pages, list) or not pages or len(pages) > len(self.PAGE_CHOICES):
            raise serializers.ValidationError({"pages": "Choose one or more unique advertising pages."})
        if any(not isinstance(page, str) or page not in self.PAGE_CHOICES for page in pages):
            raise serializers.ValidationError({"pages": "Choose valid advertising pages."})
        if len(set(pages)) != len(pages):
            raise serializers.ValidationError({"pages": "Choose one or more unique advertising pages."})
        if package and (len(pages) > package.max_pages or not set(pages).issubset(set(package.allowed_pages))):
            raise serializers.ValidationError({"pages": "Selected pages are not included in this advertising plan."})
        attrs["pages"] = pages
        starts = attrs.get("starts_at", getattr(self.instance, "starts_at", None))
        ends = attrs.get("ends_at", getattr(self.instance, "ends_at", None))
        if starts and ends and ends <= starts:
            raise serializers.ValidationError({"ends_at": "End time must be after start time."})
        return attrs

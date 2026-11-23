from django.conf import settings
from rest_framework import serializers

from .models import AdCampaign, AdAdvertiser, AdPlacement, AdWaitlistEntry
from apps.accounts.models import User


class AdCampaignSerializer(serializers.ModelSerializer):
    PAGE_CHOICES = {"homepage", "marketplace", "lost_found", "announcements", "jobs"}
    image = serializers.ImageField(write_only=True, required=False, allow_null=True)
    target_url = serializers.CharField(max_length=500)
    image_url = serializers.SerializerMethodField()
    advertiser_email = serializers.EmailField(write_only=True, required=False)
    owner_email = serializers.SerializerMethodField()
    advertiser_name = serializers.SerializerMethodField()
    package_name = serializers.SerializerMethodField()
    placement_key = serializers.CharField(source="ad_placement.key", read_only=True, allow_null=True)
    ad_placements = serializers.PrimaryKeyRelatedField(many=True, queryset=AdPlacement.objects.filter(is_active=True), required=False)
    placement_keys = serializers.SerializerMethodField()
    has_successful_payment = serializers.SerializerMethodField()
    payment_status = serializers.SerializerMethodField()
    refund_status = serializers.SerializerMethodField()
    paid_amount_kobo = serializers.SerializerMethodField()

    class Meta:
        model = AdCampaign
        fields = ("id", "package", "package_name", "plan_price_kobo", "payment_required_snapshot", "business_name", "advertiser_email", "owner_email", "advertiser_name", "pages", "ad_placement", "ad_placements", "placement_key", "placement_keys", "business_advertiser", "display_image", "price_kobo", "duration_days", "title", "description", "image", "image_url", "target_url", "placement", "status", "click_count", "impression_count", "starts_at", "ends_at", "approved_at", "created_at", "reviewer_note", "advertising_policy_accepted", "advertising_policy_accepted_at", "has_successful_payment", "paid_amount_kobo", "payment_status", "refund_status")
        read_only_fields = ("id", "status", "plan_price_kobo", "payment_required_snapshot", "price_kobo", "duration_days", "click_count", "impression_count", "approved_at", "business_advertiser", "created_at", "reviewer_note", "advertising_policy_accepted", "advertising_policy_accepted_at", "has_successful_payment", "paid_amount_kobo", "payment_status", "refund_status")

    def get_fields(self):
        fields = super().get_fields()
        request = self.context.get("request")
        if not (request and request.user.is_staff):
            fields.pop("owner_email", None)
            fields["advertiser_email"].read_only = True
        return fields

    def get_package_name(self, obj):
        return obj.plan_name_snapshot or (obj.package.name if obj.package_id else "")

    def _payments(self, obj):
        return sorted(obj.payments.all(), key=lambda payment: (payment.created_at, payment.pk), reverse=True)

    def _can_view_payment(self, obj):
        request = self.context.get("request")
        return bool(request and request.user.is_authenticated and (request.user.is_staff or obj.advertiser_id == request.user.pk))

    def get_has_successful_payment(self, obj):
        return any(payment.status == "success" for payment in self._payments(obj)) if self._can_view_payment(obj) else False

    def get_paid_amount_kobo(self, obj):
        return sum(payment.amount_kobo for payment in self._payments(obj) if payment.status == "success") if self._can_view_payment(obj) else 0

    def get_payment_status(self, obj):
        if not self._can_view_payment(obj):
            return None
        payments = self._payments(obj)
        return payments[0].status if payments else None

    def get_refund_status(self, obj):
        if not self._can_view_payment(obj):
            return ""
        payments = self._payments(obj)
        return next((payment.refund_status for payment in payments if payment.status == "success" and payment.refund_status), "")

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not self._can_view_payment(instance):
            data.pop("reviewer_note", None)
        return data

    def get_image_url(self, obj):
        if not obj.image:
            return None
        # Approved campaign artwork is public content. Let Cloudinary's CDN
        # serve the transformed image instead of using a Django worker.
        if obj.status == AdCampaign.Status.ACTIVE and getattr(obj.image.storage, "cloudinary_enabled", False):
            return obj.image.url
        request = self.context.get("request")
        path = f"/api/ads/{obj.pk}/image/"
        return request.build_absolute_uri(path) if request else path

    def get_advertiser_name(self, obj):
        if obj.business_advertiser_id:
            return obj.business_advertiser.business_name
        return obj.advertiser.get_full_name().strip() or obj.advertiser.username if obj.advertiser_id else "Tarmac advertiser"

    def get_owner_email(self, obj):
        if obj.business_advertiser_id:
            return obj.business_advertiser.email
        return obj.advertiser.email if obj.advertiser_id else ""

    def get_placement_keys(self, obj):
        keys = list(obj.ad_placements.values_list("key", flat=True))
        if not keys and obj.ad_placement_id:
            keys = [obj.ad_placement.key]
        return keys

    def validate_image(self, image):
        if image.size > 5 * 1024 * 1024:
            raise serializers.ValidationError("Ad images must be 5 MB or smaller.")
        return image

    def validate_target_url(self, value):
        if value:
            from config.url_validation import normalize_public_contact_url
            try:
                return normalize_public_contact_url(value, require_https=not settings.DEBUG)
            except ValueError as exc:
                raise serializers.ValidationError(str(exc)) from exc
        return value

    def validate(self, attrs):
        request = self.context.get("request")
        is_admin = bool(request and request.user.is_staff)
        if "admin_amount_kobo" in getattr(self, "initial_data", {}):
            raise serializers.ValidationError({"admin_amount_kobo": "Campaign prices come from the selected plan. Edit plan prices in Advertising settings; existing campaigns keep their submitted price."})
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
        package = attrs.get("package", getattr(self.instance, "package", None))
        placement = attrs.get("ad_placement", getattr(self.instance, "ad_placement", None))
        placements = attrs.get("ad_placements")
        if placements is None and self.instance:
            placements = list(self.instance.ad_placements.all())
        placements = list(placements or [])
        if placement and not placements:
            placements = [placement]
        if not placement and placements:
            placement = placements[0]
        if self.instance:
            immutable_terms = {
                "package": (lambda value: getattr(value, "pk", None), self.instance.package_id),
                "pages": (lambda value: value, self.instance.pages),
                "ad_placement": (lambda value: getattr(value, "pk", None), self.instance.ad_placement_id),
                "ad_placements": (lambda value: sorted(item.pk for item in value), sorted(self.instance.ad_placements.values_list("pk", flat=True))),
                "starts_at": (lambda value: value, self.instance.starts_at),
                "ends_at": (lambda value: value, self.instance.ends_at),
            }
            for field, (normalize, current) in immutable_terms.items():
                if field in attrs and normalize(attrs[field]) != current:
                    raise serializers.ValidationError({field: "Campaign plan, price, dates and placements are fixed after submission. Create a new campaign to change those terms."})
        if package and not self.instance and not package.is_active:
            raise serializers.ValidationError({"package": "Choose an active advertising package."})
        if placement:
            if not placement.is_active:
                raise serializers.ValidationError({"ad_placement": "This advertising placement is unavailable."})
        if placements:
            if any(not item.is_active for item in placements):
                raise serializers.ValidationError({"ad_placements": "One or more advertising placements are unavailable."})
            if len({item.pk for item in placements}) != len(placements):
                raise serializers.ValidationError({"ad_placements": "Choose each placement only once."})
            if package and not self.instance and len(placements) > package.max_pages:
                raise serializers.ValidationError({"ad_placements": f"This plan allows at most {package.max_pages} placement(s)."})
            allowed = set(package.allowed_placements or []) if package else set()
            if package and not allowed:
                allowed_pages = set(package.allowed_pages or [])
                allowed = {item.key for item in placements if item.page in allowed_pages}
            if package and not self.instance and any(item.key not in allowed for item in placements):
                raise serializers.ValidationError({"ad_placements": "One or more placements are not included in this plan."})
            attrs["ad_placements"] = placements
            attrs["ad_placement"] = placement
            attrs["pages"] = sorted({item.page for item in placements})
        if placement:
            attrs["placement"] = placement.page
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
        if package and not self.instance and (len(pages) > package.max_pages or not set(pages).issubset(set(package.allowed_pages))):
            raise serializers.ValidationError({"pages": "Selected pages are not included in this advertising plan."})
        attrs["pages"] = pages
        starts = attrs.get("starts_at", getattr(self.instance, "starts_at", None))
        ends = attrs.get("ends_at", getattr(self.instance, "ends_at", None))
        if starts and ends and ends <= starts:
            raise serializers.ValidationError({"ends_at": "End time must be after start time."})
        return attrs


class AdPlacementSerializer(serializers.ModelSerializer):
    page_label = serializers.CharField(source="get_page_display", read_only=True)

    class Meta:
        model = AdPlacement
        fields = ("id", "key", "page", "page_label", "position", "description", "max_slots", "limited_slots_threshold", "is_active", "is_exclusive", "sort_order", "created_at", "updated_at")
        read_only_fields = ("id", "created_at", "updated_at")

    def validate(self, attrs):
        max_slots = attrs.get("max_slots", getattr(self.instance, "max_slots", 5))
        limited = attrs.get("limited_slots_threshold", getattr(self.instance, "limited_slots_threshold", 2))
        if max_slots < 1 or not 0 <= limited <= max_slots:
            raise serializers.ValidationError({"max_slots": "Capacity must be positive and the limited-slots threshold cannot exceed it."})
        return attrs


class AdWaitlistEntrySerializer(serializers.ModelSerializer):
    business_name = serializers.CharField(source="advertiser.business_name", read_only=True)
    contact_name = serializers.CharField(source="advertiser.contact_name", read_only=True)
    email = serializers.EmailField(source="advertiser.email", read_only=True)
    placement_key = serializers.CharField(source="placement.key", read_only=True)
    placement_name = serializers.CharField(source="placement.position", read_only=True)

    class Meta:
        model = AdWaitlistEntry
        fields = ("id", "business_name", "contact_name", "email", "placement", "placement_key", "placement_name", "starts_at", "ends_at", "status", "offer_expires_at", "created_at")
        read_only_fields = fields

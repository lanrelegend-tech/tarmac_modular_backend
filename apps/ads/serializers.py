from django.conf import settings
from rest_framework import serializers

from .models import AdCampaign


class AdCampaignSerializer(serializers.ModelSerializer):
    image = serializers.ImageField(write_only=True)
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = AdCampaign
        fields = ("id", "title", "description", "image", "image_url", "target_url", "placement", "status", "starts_at", "ends_at", "created_at")
        read_only_fields = ("id", "status", "created_at")

    def get_image_url(self, obj):
        request = self.context.get("request")
        path = f"/api/ads/{obj.pk}/image/"
        return request.build_absolute_uri(path) if request else path

    def validate_image(self, image):
        if image.size > 5 * 1024 * 1024:
            raise serializers.ValidationError("Ad images must be 5 MB or smaller.")
        return image

    def validate_target_url(self, value):
        if not settings.DEBUG and not value.lower().startswith("https://"):
            raise serializers.ValidationError("Ad destination URLs must use HTTPS.")
        return value

    def validate(self, attrs):
        starts = attrs.get("starts_at", getattr(self.instance, "starts_at", None))
        ends = attrs.get("ends_at", getattr(self.instance, "ends_at", None))
        if starts and ends and ends <= starts:
            raise serializers.ValidationError({"ends_at": "End time must be after start time."})
        return attrs

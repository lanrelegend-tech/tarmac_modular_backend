from rest_framework import serializers
from .models import LostFoundReport,ItemMatch
from django.utils import timezone
class LostFoundReportSerializer(serializers.ModelSerializer):
    reporter_id=serializers.IntegerField(read_only=True)
    image = serializers.ImageField(write_only=True, required=False, allow_null=True)
    identifying_details = serializers.CharField(write_only=True, required=False, allow_blank=True)
    image_url = serializers.SerializerMethodField()
    class Meta: model=LostFoundReport; fields=("id","reporter_id","report_type","title","description","category","colour","location","incident_date","identifying_details","image","image_url","status","created_at"); read_only_fields=("status","created_at")
    def get_image_url(self, obj):
        if not obj.image:
            return None
        request = self.context.get("request")
        path = f"/api/lost-found/reports/{obj.pk}/image-file/"
        return request.build_absolute_uri(path) if request else path
    def validate_image(self, image):
        if image.size > 5 * 1024 * 1024:
            raise serializers.ValidationError("Images must be 5 MB or smaller.")
        return image
    def validate_incident_date(self, value):
        if value > timezone.localdate():
            raise serializers.ValidationError("The incident date cannot be in the future.")
        return value
class ItemMatchSerializer(serializers.ModelSerializer):
    class Meta: model=ItemMatch; fields="__all__"

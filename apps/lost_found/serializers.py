from rest_framework import serializers
from .models import LostFoundReport,ItemMatch,ItemClaim
from django.utils import timezone
from apps.administration.campuses import validate_campus_choice
class LostFoundReportSerializer(serializers.ModelSerializer):
    campus = serializers.CharField(required=True, max_length=120)
    image = serializers.ImageField(write_only=True, required=False, allow_null=True)
    identifying_details = serializers.CharField(write_only=True, required=False, allow_blank=True)
    contact_details = serializers.CharField(write_only=True, required=False, allow_blank=True)
    category_name = serializers.CharField(source="category.name", read_only=True)
    image_url = serializers.SerializerMethodField()
    is_owner = serializers.SerializerMethodField()
    reporter_public_id = serializers.SerializerMethodField()
    class Meta: model=LostFoundReport; fields=("id","public_id","report_type","title","description","category","category_name","colour","location","campus","incident_date","identifying_details","contact_details","image","image_url","status","created_at","is_owner","reporter_public_id"); read_only_fields=("public_id","status","created_at","is_owner","reporter_public_id")
    def validate_campus(self, value): return validate_campus_choice(value)
    def get_is_owner(self, obj):
        request = self.context.get("request")
        return bool(request and request.user.is_authenticated and obj.reporter_id == request.user.pk)
    def get_reporter_public_id(self, obj):
        user = obj.reporter
        if user.is_active and not user.is_suspended and user.is_student_verified and user.public_profile_enabled:
            return str(user.public_id)
        return ""
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
    lost_title = serializers.CharField(source="lost_report.title", read_only=True)
    found_title = serializers.CharField(source="found_report.title", read_only=True)
    class Meta: model=ItemMatch; fields=("id", "lost_report", "lost_title", "found_report", "found_title", "score", "reasons", "created_at"); read_only_fields=fields

class ItemClaimSerializer(serializers.ModelSerializer):
    claimant_name = serializers.SerializerMethodField()
    evidence_url = serializers.SerializerMethodField()
    class Meta:
        model = ItemClaim
        fields = ("id", "report", "claimant_name", "details", "evidence", "evidence_name", "evidence_url", "status", "reviewer_note", "created_at", "reviewed_at")
        read_only_fields = ("id", "claimant", "claimant_name", "evidence_name", "evidence_url", "status", "reviewer_note", "created_at", "reviewed_at")
    def get_claimant_name(self, obj): return obj.claimant.get_full_name().strip() or obj.claimant.username
    def get_evidence_url(self, obj):
        if not obj.evidence: return None
        request = self.context.get("request")
        path = f"/api/lost-found/claims/{obj.pk}/evidence/"
        return request.build_absolute_uri(path) if request else path
    def validate_evidence(self, value):
        if value and value.size > 5 * 1024 * 1024: raise serializers.ValidationError("Evidence must be 5 MB or smaller.")
        if value and value.content_type not in ("image/jpeg", "image/png", "image/webp", "application/pdf"): raise serializers.ValidationError("Use JPG, PNG, WEBP or PDF evidence.")
        if value and value.content_type == "application/pdf":
            from config.upload_validation import validate_pdf_upload
            try:
                validate_pdf_upload(value)
            except ValueError as error:
                raise serializers.ValidationError(str(error)) from error
        return value
    def validate_report(self, report):
        if report.report_type != LostFoundReport.Type.FOUND or not report.is_active:
            raise serializers.ValidationError("Claims can only be made against an active found-item report.")
        return report

    def create(self, validated_data):
        evidence = validated_data.get("evidence")
        if evidence and evidence.content_type.startswith("image/"):
            from config.storage import optimize_uploaded_image
            try:
                optimized = optimize_uploaded_image(evidence, max_size=(1440, 1440), quality=78)
            except ValueError as error:
                raise serializers.ValidationError({"evidence": str(error)}) from error
            evidence.name = f"{evidence.name.rsplit('.', 1)[0]}.webp"
            optimized.name = evidence.name
            validated_data["evidence"] = optimized
        return super().create(validated_data)

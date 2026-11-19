from rest_framework import serializers
from apps.accounts.models import StudentVerification, User
from apps.reviews.models import SellerReview
from .models import UserReport,ReportEvidence,Announcement,AdminLog
class UserReportSerializer(serializers.ModelSerializer):
    evidence_images = serializers.ListField(child=serializers.ImageField(), write_only=True, required=False, max_length=5)
    evidence = serializers.SerializerMethodField()

    class Meta:
        model=UserReport
        fields=("id","reporter","reported_user","product","lost_found_report","reason","details","is_resolved","created_at","evidence_images","evidence")
        read_only_fields=("reporter","created_at")

    def get_fields(self):
        fields = super().get_fields()
        request = self.context.get("request")
        if not (request and request.user.is_staff):
            fields["is_resolved"].read_only = True
        return fields

    def validate_evidence_images(self, images):
        for image in images:
            if image.size > 5 * 1024 * 1024:
                raise serializers.ValidationError("Each screenshot must be 5 MB or smaller.")
        return images

    def validate(self, attrs):
        if self.instance is None and not any(attrs.get(key) for key in ("reported_user", "product", "lost_found_report")):
            raise serializers.ValidationError("Choose the student, listing, or lost-and-found post to report.")
        return attrs

    def create(self, validated_data):
        images = validated_data.pop("evidence_images", [])
        report = UserReport.objects.create(**validated_data)
        ReportEvidence.objects.bulk_create([ReportEvidence(report=report, image=image) for image in images])
        return report

    def get_evidence(self, obj):
        request = self.context.get("request")
        return [{"id": image.pk, "image_url": request.build_absolute_uri(f"/api/admin-tools/report-evidence/{image.pk}/image/") if request else f"/api/admin-tools/report-evidence/{image.pk}/image/"} for image in obj.evidence.all()]
class AnnouncementSerializer(serializers.ModelSerializer):
    class Meta: model=Announcement; fields="__all__"; read_only_fields=("created_by","created_at","is_published","published_at")
class AdminLogSerializer(serializers.ModelSerializer):
    class Meta: model=AdminLog; fields="__all__"

class AdminVerificationSerializer(serializers.ModelSerializer):
    student_id_image_url = serializers.SerializerMethodField()
    student_email = serializers.EmailField(source="user.email", read_only=True)
    student_name = serializers.SerializerMethodField()
    class Meta:
        model = StudentVerification
        fields = ("id", "user", "student_email", "student_name", "submitted_matric_number", "student_id_image_url", "status", "reviewer_note", "created_at", "reviewed_by", "reviewed_at")
        read_only_fields = ("id", "user", "submitted_matric_number", "student_id_image_url", "created_at", "reviewed_by", "reviewed_at")

    def get_student_id_image_url(self, obj):
        request = self.context.get("request")
        path = f"/api/admin-tools/verifications/{obj.pk}/student-id/"
        return request.build_absolute_uri(path) if request else path

    def get_student_name(self, obj):
        return obj.user.get_full_name().strip() or obj.user.username

    def validate_status(self, status):
        if status not in (StudentVerification.Status.APPROVED, StudentVerification.Status.REJECTED):
            raise serializers.ValidationError("Choose approved or rejected.")
        return status

class AdminReviewSerializer(serializers.ModelSerializer):
    reviewer_name = serializers.SerializerMethodField()
    seller_name = serializers.SerializerMethodField()
    class Meta:
        model = SellerReview
        fields = ("id", "reviewer", "reviewer_name", "seller", "seller_name", "order", "rating", "comment", "status", "created_at")
        read_only_fields = ("id", "reviewer", "reviewer_name", "seller", "seller_name", "order", "rating", "comment", "created_at")

    def get_reviewer_name(self, obj):
        return obj.reviewer.get_full_name().strip() or obj.reviewer.username

    def get_seller_name(self, obj):
        return obj.seller.get_full_name().strip() or obj.seller.username

    def validate_status(self, status):
        if status not in SellerReview.Status.values:
            raise serializers.ValidationError("Choose pending, approved, or hidden.")
        return status

class AdminAccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "email", "username", "role", "is_active", "is_suspended", "is_student_verified", "email_verified", "date_joined")
        read_only_fields = ("id", "email", "username", "role", "is_student_verified", "email_verified", "date_joined")

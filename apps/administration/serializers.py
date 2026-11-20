from rest_framework import serializers
from apps.accounts.models import StudentVerification, User
from apps.reviews.models import SellerReview
from .models import UserReport,ReportEvidence,Announcement,AdminLog,SupportTicket,SupportMessage,SupportAttachment,PlatformSettings,SubscriptionPlan,StudentSubscription
class UserReportSerializer(serializers.ModelSerializer):
    evidence_images = serializers.ListField(child=serializers.ImageField(), write_only=True, required=False, max_length=5)
    evidence = serializers.SerializerMethodField()

    class Meta:
        model=UserReport
        fields=("id","reporter","reported_user","product","lost_found_report","reason","details","is_resolved","status","reviewer_note","assigned_to","created_at","evidence_images","evidence")
        read_only_fields=("reporter","created_at","assigned_to")

    def get_fields(self):
        fields = super().get_fields()
        request = self.context.get("request")
        if not (request and request.user.is_staff):
            for key in ("is_resolved", "status", "reviewer_note", "assigned_to"):
                fields[key].read_only = True
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
    created_by_name = serializers.SerializerMethodField()
    class Meta: model=Announcement; fields="__all__"; read_only_fields=("created_by","created_by_name","created_at","is_published","published_at","delivery_cursor","delivery_completed","recipient_count")

    def get_created_by_name(self, obj):
        return obj.created_by.get_full_name().strip() or obj.created_by.username
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
        fields = ("id", "email", "username", "first_name", "last_name", "matric_number", "phone_number", "department", "faculty", "level", "campus", "role", "is_active", "is_suspended", "is_student_verified", "email_verified", "date_joined")
        read_only_fields = ("id", "email", "username", "first_name", "last_name", "matric_number", "phone_number", "department", "faculty", "level", "campus", "role", "is_student_verified", "email_verified", "date_joined")

class AdminConversationMessageSerializer(serializers.ModelSerializer):
    sender_name = serializers.SerializerMethodField()
    is_staff = serializers.BooleanField(source="sender.is_staff", read_only=True)
    class Meta:
        from apps.messaging.models import Message
        model = Message
        fields = ("id", "sender", "sender_name", "is_staff", "body", "created_at", "read_at")
    def get_sender_name(self, obj): return obj.sender.get_full_name().strip() or obj.sender.username

class AdminConversationSerializer(serializers.ModelSerializer):
    participants_data = serializers.SerializerMethodField()
    messages = serializers.SerializerMethodField()
    unread_count = serializers.IntegerField(read_only=True)
    class Meta:
        from apps.messaging.models import Conversation
        model = Conversation
        fields = ("id", "participants_data", "messages", "unread_count", "status", "created_at", "updated_at")
    def get_participants_data(self, obj):
        return [{"id": user.pk, "name": user.get_full_name().strip() or user.username, "email": user.email, "phone": user.phone_number, "matric_number": user.matric_number, "is_staff": user.is_staff} for user in obj.participants.all()]
    def get_messages(self, obj):
        messages = getattr(obj, "recent_messages", None)
        if messages is None: messages = list(obj.messages.select_related("sender").order_by("-created_at")[:50])
        return AdminConversationMessageSerializer(list(reversed(messages)), many=True, context=self.context).data

class SubscriptionPlanSerializer(serializers.ModelSerializer):
    class Meta: model = SubscriptionPlan; fields = ("id", "name", "description", "price_kobo", "duration_days", "placement", "max_images", "visibility", "features", "is_popular", "color", "is_active", "created_at", "updated_at"); read_only_fields = ("id", "created_at", "updated_at")
    def validate_duration_days(self, value):
        if not 1 <= value <= 366: raise serializers.ValidationError("Duration must be between 1 and 366 days.")
        return value

class StudentSubscriptionSerializer(serializers.ModelSerializer):
    plan_name = serializers.CharField(source="plan.name", read_only=True)
    student_email = serializers.EmailField(source="user.email", read_only=True)
    student_name = serializers.SerializerMethodField()
    class Meta: model = StudentSubscription; fields = ("id", "user", "student_name", "student_email", "plan", "plan_name", "amount_kobo", "payment_reference", "status", "starts_at", "ends_at", "created_at"); read_only_fields = fields
    def get_student_name(self, obj): return obj.user.get_full_name().strip() or obj.user.username


class SupportAttachmentSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()
    class Meta:
        model = SupportAttachment
        fields = ("id", "original_name", "content_type", "file_url", "created_at")
        read_only_fields = fields
    def get_file_url(self, obj):
        request = self.context.get("request")
        path = f"/api/admin-tools/support/attachments/{obj.pk}/file/"
        return request.build_absolute_uri(path) if request else path


class SupportMessageSerializer(serializers.ModelSerializer):
    attachments = SupportAttachmentSerializer(many=True, read_only=True)
    sender_name = serializers.SerializerMethodField()
    class Meta:
        model = SupportMessage
        fields = ("id", "sender", "sender_name", "body", "attachments", "created_at")
        read_only_fields = fields
    def get_sender_name(self, obj):
        return obj.sender.get_full_name().strip() or obj.sender.username


class SupportTicketSerializer(serializers.ModelSerializer):
    messages = SupportMessageSerializer(many=True, read_only=True)
    initial_message = serializers.CharField(write_only=True, required=False, max_length=5000)
    attachment_files = serializers.ListField(child=serializers.FileField(), write_only=True, required=False, max_length=5)
    user_name = serializers.SerializerMethodField()
    class Meta:
        model = SupportTicket
        fields = ("id", "user", "user_name", "subject", "category", "status", "priority", "messages", "initial_message", "attachment_files", "created_at", "updated_at")
        read_only_fields = ("id", "user", "user_name", "status", "messages", "created_at", "updated_at")
    def get_fields(self):
        fields = super().get_fields()
        if self.context.get("request") and self.context["request"].user.is_staff:
            fields["status"].read_only = False
            fields["priority"].read_only = False
        return fields
    def validate(self, attrs):
        if self.instance is None and not attrs.get("initial_message", "").strip():
            raise serializers.ValidationError({"initial_message": "Describe the issue in your first message."})
        uploads = attrs.get("attachment_files", [])
        if len(uploads) > 5:
            raise serializers.ValidationError({"attachment_files": "Attach at most five files."})
        for upload in uploads:
            if upload.size > 5 * 1024 * 1024:
                raise serializers.ValidationError({"attachment_files": "Each file must be 5 MB or smaller."})
            if upload.content_type not in ("image/jpeg", "image/png", "image/webp", "application/pdf"):
                raise serializers.ValidationError({"attachment_files": "Only JPG, PNG, WEBP and PDF files are accepted."})
        return attrs
    def create(self, validated_data):
        from django.db import transaction
        from django.utils.text import get_valid_filename
        uploads = validated_data.pop("attachment_files", [])
        first_message = validated_data.pop("initial_message")
        with transaction.atomic():
            ticket = SupportTicket.objects.create(**validated_data)
            message = SupportMessage.objects.create(ticket=ticket, sender=self.context["request"].user, body=first_message)
            for upload in uploads:
                SupportAttachment.objects.create(message=message, file=upload, original_name=get_valid_filename(upload.name)[:255], content_type=upload.content_type)
        return ticket

class PlatformSettingsSerializer(serializers.ModelSerializer):
    site_logo_url = serializers.SerializerMethodField()
    site_logo = serializers.ImageField(write_only=True, required=False, allow_null=True)
    class Meta:
        model = PlatformSettings
        fields = ("site_name", "site_logo", "site_logo_url", "tagline", "support_email", "contact_email", "maintenance_mode", "maintenance_message", "marketplace_enabled", "lost_found_enabled", "messaging_enabled", "ads_enabled", "paystack_enabled", "listing_fee_enabled", "listing_fee_kobo", "ad_fee_enabled", "ad_fee_kobo", "subscription_fee_enabled", "registration_enabled", "listing_approval_required", "max_product_images", "student_verification_enabled", "require_student_id", "email_verification_required", "auto_approve_students", "featured_products_enabled", "reviews_enabled", "review_moderation_enabled", "announcements_enabled", "email_notifications_enabled", "report_notifications_enabled", "terms_text", "privacy_text", "community_guidelines_text", "updated_at")
        read_only_fields = ("updated_at",)
    def get_site_logo_url(self, obj):
        if not obj.site_logo: return ""
        request = self.context.get("request")
        return request.build_absolute_uri(obj.site_logo.url) if request else obj.site_logo.url
    def validate_site_logo(self, image):
        if image and image.size > 2 * 1024 * 1024: raise serializers.ValidationError("The logo must be 2 MB or smaller.")
        if image and image.content_type not in ("image/jpeg", "image/png", "image/webp"): raise serializers.ValidationError("Use a PNG, JPG or WEBP logo.")
        return image
    def validate_max_product_images(self, value):
        if not 1 <= value <= 10:
            raise serializers.ValidationError("Maximum product images must be between 1 and 10.")
        return value

class AdminPaymentSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source="user.email", read_only=True)
    product_title = serializers.CharField(source="product.title", read_only=True, allow_null=True)
    advertisement_title = serializers.CharField(source="advertisement.title", read_only=True, allow_null=True)
    subscription_plan_name = serializers.CharField(source="subscription_plan.name", read_only=True, allow_null=True)
    class Meta:
        from apps.payments.models import Payment
        model = Payment
        fields = ("id", "user", "user_email", "product", "product_title", "advertisement", "advertisement_title", "subscription_plan", "subscription_plan_name", "reference", "amount_kobo", "currency", "status", "created_at", "paid_at")

class AdminLostFoundSerializer(serializers.ModelSerializer):
    reporter_id = serializers.IntegerField(read_only=True)
    category_name = serializers.CharField(source="category.name", read_only=True, allow_null=True)
    image_url = serializers.SerializerMethodField()
    class Meta:
        from apps.lost_found.models import LostFoundReport
        model = LostFoundReport
        fields = ("id", "reporter_id", "report_type", "title", "description", "category", "category_name", "colour", "location", "incident_date", "image_url", "status", "is_active", "created_at")
        read_only_fields = ("id", "reporter_id", "report_type", "title", "description", "category", "category_name", "colour", "location", "incident_date", "image_url", "created_at")
    def get_image_url(self, obj):
        if not obj.image: return None
        request = self.context.get("request")
        path = f"/api/admin-tools/lost-found/{obj.pk}/image/"
        return request.build_absolute_uri(path) if request else path

class AdminItemClaimSerializer(serializers.ModelSerializer):
    claimant_name = serializers.SerializerMethodField()
    report_title = serializers.CharField(source="report.title", read_only=True)
    class Meta:
        from apps.lost_found.models import ItemClaim
        model = ItemClaim
        fields = ("id", "report", "report_title", "claimant", "claimant_name", "details", "evidence_name", "status", "reviewer_note", "created_at", "reviewed_at")
        read_only_fields = ("id", "report", "report_title", "claimant", "claimant_name", "details", "evidence_name", "created_at", "reviewed_at")
    def get_claimant_name(self, obj): return obj.claimant.get_full_name().strip() or obj.claimant.username

class AdminItemMatchSerializer(serializers.ModelSerializer):
    lost_title = serializers.CharField(source="lost_report.title", read_only=True)
    found_title = serializers.CharField(source="found_report.title", read_only=True)
    lost_student_id = serializers.IntegerField(source="lost_report.reporter_id", read_only=True)
    found_student_id = serializers.IntegerField(source="found_report.reporter_id", read_only=True)
    class Meta:
        from apps.lost_found.models import ItemMatch
        model = ItemMatch
        fields = ("id", "lost_report", "lost_title", "lost_student_id", "found_report", "found_title", "found_student_id", "score", "reasons", "is_reviewed", "is_dismissed", "created_at")
        read_only_fields = ("id", "lost_report", "lost_title", "lost_student_id", "found_report", "found_title", "found_student_id", "score", "reasons", "created_at")

class AdminProductSerializer(serializers.ModelSerializer):
    seller_id = serializers.IntegerField(read_only=True)
    seller_name = serializers.SerializerMethodField()
    image_url = serializers.SerializerMethodField()
    seller_email = serializers.EmailField(source="seller.email", read_only=True)
    category_name = serializers.CharField(source="category.name", read_only=True)
    class Meta:
        from apps.products.models import Product
        model = Product
        fields = ("id", "seller_id", "seller_name", "seller_email", "category", "category_name", "title", "description", "condition", "price", "location", "status", "is_featured", "featured_until", "image_url", "created_at")
        read_only_fields = ("id", "seller_id", "seller_name", "seller_email", "category", "category_name", "title", "description", "condition", "price", "location", "image_url", "created_at")
    def get_image_url(self, obj):
        if not obj.images.exists(): return None
        request = self.context.get("request")
        path = f"/api/admin-tools/products/{obj.pk}/image/"
        return request.build_absolute_uri(path) if request else path
    def get_seller_name(self, obj):
        return obj.seller.get_full_name().strip() or obj.seller.username

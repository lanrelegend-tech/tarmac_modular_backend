import os
from rest_framework import serializers
from django.utils import timezone
from apps.accounts.models import StudentVerification, User, AnonymousMarketplaceIdentity
from apps.reviews.models import ReviewReport, SellerReview
from .models import UserReport,ReportEvidence,Announcement,AdminLog,SupportTicket,SupportMessage,SupportAttachment,PlatformSettings,SubscriptionPlan,StudentSubscription,default_notification_channels
from apps.ads.models import AdPlacement
class UserReportSerializer(serializers.ModelSerializer):
    evidence_images = serializers.ListField(child=serializers.ImageField(), write_only=True, required=False, max_length=5)
    evidence = serializers.SerializerMethodField()
    target_identity = serializers.CharField(write_only=True, required=False, allow_blank=True)
    reported_user_name = serializers.SerializerMethodField()
    reported_user_email = serializers.EmailField(source="reported_user.email", read_only=True)
    conversation_history = serializers.SerializerMethodField()

    class Meta:
        model=UserReport
        fields=("id","reporter","reported_user","reported_user_name","reported_user_email","product","lost_found_report","conversation","conversation_history","target_identity","target_identity_type","target_identity_public_id","target_identity_name","marketplace_identity","reason","details","is_resolved","status","reviewer_note","assigned_to","created_at","evidence_images","evidence")
        read_only_fields=("reporter","created_at","assigned_to")

    def get_fields(self):
        fields = super().get_fields()
        request = self.context.get("request")
        if not (request and request.user.is_staff):
            fields["reported_user"].read_only = True
            fields["reported_user_name"].read_only = True
            fields["marketplace_identity"].read_only = True
            fields["target_identity_type"].read_only = True
            fields["target_identity_public_id"].read_only = True
            fields["target_identity_name"].read_only = True
            for key in ("is_resolved", "status", "reviewer_note", "assigned_to"):
                fields[key].read_only = True
        else:
            fields["target_identity"].read_only = True
        return fields

    def validate_evidence_images(self, images):
        for image in images:
            if image.size > 5 * 1024 * 1024:
                raise serializers.ValidationError("Each screenshot must be 5 MB or smaller.")
        return images

    def validate(self, attrs):
        if self.instance is None and not any(attrs.get(key) for key in ("reported_user", "product", "lost_found_report", "conversation")):
            raise serializers.ValidationError("Choose the student, listing, or lost-and-found post to report.")
        request = self.context["request"]
        if request.user.is_staff:
            return attrs
        from apps.products.models import Product
        product = attrs.get("product")
        target = attrs.pop("target_identity", "")
        user = None
        alias = None
        if product:
            if product.seller_id == request.user.pk:
                raise serializers.ValidationError({"product": "You cannot report your own listing."})
            user = product.seller
            if product.identity_type == Product.IdentityType.ANONYMOUS:
                alias = product.anonymous_identity
                attrs.update(marketplace_identity=alias, target_identity_type="anonymous", target_identity_public_id=str(alias.public_id) if alias else "", target_identity_name=alias.handle if alias else "Anonymous identity")
            else:
                attrs.update(target_identity_type="normal", target_identity_public_id=str(user.public_id), target_identity_name=user.username if not user.public_name_enabled else (user.get_full_name().strip() or user.username))
        elif target:
            alias = AnonymousMarketplaceIdentity.objects.select_related("user").filter(public_id=target, is_enabled=True).first()
            if alias:
                user = alias.user
                attrs.update(marketplace_identity=alias, target_identity_type="anonymous", target_identity_public_id=str(alias.public_id), target_identity_name=alias.handle)
            else:
                user = User.objects.filter(public_id=target, is_active=True).first()
                if not user:
                    raise serializers.ValidationError({"target_identity": "Choose a valid public marketplace identity."})
                attrs.update(target_identity_type="normal", target_identity_public_id=str(user.public_id), target_identity_name=user.username if not user.public_name_enabled else (user.get_full_name().strip() or user.username))
        elif attrs.get("conversation"):
            from apps.messaging.models import ConversationParticipantSnapshot
            conversation = attrs["conversation"]
            if not conversation.participants.filter(pk=request.user.pk).exists():
                raise serializers.ValidationError({"conversation": "You can only report a conversation you took part in."})
            other = conversation.participants.exclude(pk=request.user.pk).filter(is_staff=False).first()
            if not other:
                raise serializers.ValidationError({"conversation": "This conversation has no other student to report."})
            user = other
            snapshot = ConversationParticipantSnapshot.objects.filter(conversation=conversation, user=other).first()
            if snapshot:
                attrs.update(target_identity_type=snapshot.identity_type, target_identity_public_id=snapshot.public_identity_id, target_identity_name=snapshot.display_name)
                if snapshot.identity_type == "anonymous":
                    alias = AnonymousMarketplaceIdentity.objects.filter(user=other, public_id=snapshot.public_identity_id).first()
                    attrs["marketplace_identity"] = alias
        elif attrs.get("lost_found_report"):
            user = attrs["lost_found_report"].reporter
        if user and user.pk == request.user.pk:
            raise serializers.ValidationError("You cannot report your own account or listing.")
        attrs["reported_user"] = user
        return attrs

    def create(self, validated_data):
        images = validated_data.pop("evidence_images", [])
        report = UserReport.objects.create(**validated_data)
        ReportEvidence.objects.bulk_create([ReportEvidence(report=report, image=image) for image in images])
        return report

    def get_evidence(self, obj):
        request = self.context.get("request")
        return [{"id": image.pk, "image_url": request.build_absolute_uri(f"/api/admin-tools/report-evidence/{image.pk}/image/") if request else f"/api/admin-tools/report-evidence/{image.pk}/image/"} for image in obj.evidence.all()]

    def get_reported_user_name(self, obj):
        if not obj.reported_user:
            return ""
        return obj.reported_user.get_full_name().strip() or obj.reported_user.username

    def get_conversation_history(self, obj):
        request = self.context.get("request")
        view = self.context.get("view")
        if not request or not request.user.is_staff or not obj.conversation_id or getattr(view, "action", None) != "retrieve":
            return []
        from apps.messaging.models import Message
        messages = list(obj.conversation.messages.select_related("sender", "conversation").prefetch_related("attachments", "conversation__participant_snapshots").order_by("-created_at")[:50])
        return AdminConversationMessageSerializer(reversed(messages), many=True, context=self.context).data

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")
        if not (request and request.user.is_staff):
            data.pop("reported_user", None)
            data.pop("reported_user_name", None)
            data.pop("reported_user_email", None)
            data.pop("conversation_history", None)
            data.pop("marketplace_identity", None)
            data.pop("target_identity_type", None)
            data.pop("target_identity_public_id", None)
            data.pop("target_identity_name", None)
        return data
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
        fields = ("id", "user", "student_email", "student_name", "submitted_matric_number", "requested_affiliation", "student_id_image_url", "status", "reviewer_note", "created_at", "reviewed_by", "reviewed_at")
        read_only_fields = ("id", "user", "submitted_matric_number", "student_id_image_url", "created_at", "reviewed_by", "reviewed_at")

    def get_student_id_image_url(self, obj):
        if not obj.student_id_image:
            return None
        request = self.context.get("request")
        path = f"/api/admin-tools/verifications/{obj.pk}/student-id/"
        return request.build_absolute_uri(path) if request else path

    def get_student_name(self, obj):
        return obj.user.get_full_name().strip() or obj.user.username

    def validate_status(self, status):
        if status not in (StudentVerification.Status.APPROVED, StudentVerification.Status.REJECTED):
            raise serializers.ValidationError("Choose approved or rejected.")
        return status

    def validate(self, attrs):
        status = attrs.get("status", getattr(self.instance, "status", None))
        note = str(attrs.get("reviewer_note", getattr(self.instance, "reviewer_note", ""))).strip()
        if status == StudentVerification.Status.REJECTED and not note:
            raise serializers.ValidationError({"reviewer_note": "Give the student a reason and tell them what to correct before rejecting."})
        if len(note) > 3000:
            raise serializers.ValidationError({"reviewer_note": "Keep the review note under 3,000 characters."})
        attrs["reviewer_note"] = note
        return attrs

class AdminReviewSerializer(serializers.ModelSerializer):
    reviewer_name = serializers.SerializerMethodField()
    seller_name = serializers.SerializerMethodField()
    image_url = serializers.SerializerMethodField()
    sentiment = serializers.CharField(read_only=True)
    class Meta:
        model = SellerReview
        fields = ("id", "reviewer", "reviewer_name", "seller", "seller_name", "category", "conversation", "job_engagement", "identity_type", "identity_public_id", "identity_display_name", "rating", "sentiment", "comment", "image_url", "status", "created_at")
        read_only_fields = ("id", "reviewer", "reviewer_name", "seller", "seller_name", "category", "conversation", "job_engagement", "identity_type", "identity_public_id", "identity_display_name", "rating", "comment", "created_at")

    def get_reviewer_name(self, obj):
        return obj.reviewer.get_full_name().strip() or obj.reviewer.username

    def get_seller_name(self, obj):
        return obj.seller.get_full_name().strip() or obj.seller.username

    def get_image_url(self, obj):
        return obj.image.url if obj.image else None

    def validate_status(self, status):
        if status not in SellerReview.Status.values:
            raise serializers.ValidationError("Choose pending, approved, or hidden.")
        return status


class AdminReviewReportSerializer(serializers.ModelSerializer):
    reviewer_name = serializers.CharField(source="review.reviewer_display_name", read_only=True)
    identity_name = serializers.CharField(source="review.identity_display_name", read_only=True)
    review_comment = serializers.CharField(source="review.comment", read_only=True)
    reporter_email = serializers.EmailField(source="reporter.email", read_only=True)

    class Meta:
        model = ReviewReport
        fields = ("id", "review", "reviewer_name", "identity_name", "review_comment", "reporter", "reporter_email", "reason", "details", "status", "admin_note", "created_at", "resolved_at")
        read_only_fields = ("id", "review", "reviewer_name", "identity_name", "review_comment", "reporter", "reporter_email", "reason", "details", "created_at", "resolved_at")

    def validate_status(self, value):
        if value not in ReviewReport.Status.values:
            raise serializers.ValidationError("Choose open, reviewing, resolved, or dismissed.")
        return value

class AdminAccountSerializer(serializers.ModelSerializer):
    course_name = serializers.CharField(source="course.name", read_only=True, allow_null=True)
    student_status = serializers.CharField(read_only=True)
    class Meta:
        model = User
        fields = ("id", "email", "username", "first_name", "last_name", "matric_number", "phone_number", "department", "faculty", "level", "course", "course_name", "entry_year", "student_status", "student_status_override", "campus", "role", "is_active", "is_suspended", "is_student_verified", "email_verified", "date_joined")
        read_only_fields = ("id", "email", "username", "first_name", "last_name", "matric_number", "phone_number", "department", "faculty", "level", "course_name", "student_status", "student_status_override", "campus", "role", "is_active", "is_suspended", "is_student_verified", "email_verified", "date_joined")

    def validate(self, attrs):
        course = attrs.get("course", getattr(self.instance, "course", None))
        entry_year = attrs.get("entry_year", getattr(self.instance, "entry_year", None))
        if bool(course) != bool(entry_year):
            raise serializers.ValidationError("Set both course and year of entry, or leave both blank.")
        if entry_year and not 2000 <= entry_year <= timezone.localdate().year:
            raise serializers.ValidationError({"entry_year": "Enter a valid year of entry."})
        return attrs


class AdminAdministratorSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "email", "username", "first_name", "last_name", "role", "is_staff", "is_superuser", "is_active", "date_joined", "last_login")
        read_only_fields = fields

class AdminConversationMessageSerializer(serializers.ModelSerializer):
    sender_name = serializers.SerializerMethodField()
    marketplace_identity_name = serializers.SerializerMethodField()
    is_staff = serializers.BooleanField(source="sender.is_staff", read_only=True)
    attachments = serializers.SerializerMethodField()
    class Meta:
        from apps.messaging.models import Message
        model = Message
        fields = ("id", "sender", "sender_name", "marketplace_identity_name", "is_staff", "body", "attachments", "created_at", "read_at")
    def get_sender_name(self, obj): return "Tarmac Administration" if obj.sender.is_staff else (obj.sender.get_full_name().strip() or obj.sender.username)
    def get_marketplace_identity_name(self, obj):
        return obj.conversation.participant_snapshots.filter(user=obj.sender).values_list("display_name", flat=True).first() or ""
    def get_attachments(self, obj):
        from apps.messaging.serializers import MessageAttachmentSerializer
        return MessageAttachmentSerializer(obj.attachments.all(), many=True, context=self.context).data

class AdminConversationSerializer(serializers.ModelSerializer):
    participants_data = serializers.SerializerMethodField()
    messages = serializers.SerializerMethodField()
    unread_count = serializers.IntegerField(read_only=True)
    class Meta:
        from apps.messaging.models import Conversation
        model = Conversation
        fields = ("id", "participants_data", "messages", "unread_count", "status", "context_type", "context_title", "is_official", "completed_by", "completed_at", "created_at", "updated_at")
    def get_participants_data(self, obj):
        snapshots = {row.user_id: row for row in obj.participant_snapshots.all()}
        return [{"id": user.pk, "name": user.get_full_name().strip() or user.username, "email": user.email, "phone": user.phone_number, "matric_number": user.matric_number, "is_staff": user.is_staff, "marketplace_identity_type": snapshots[user.pk].identity_type if user.pk in snapshots else "normal", "marketplace_identity_name": snapshots[user.pk].display_name if user.pk in snapshots else "", "marketplace_public_id": snapshots[user.pk].public_identity_id if user.pk in snapshots else str(user.public_id)} for user in obj.participants.all()]
    def get_messages(self, obj):
        messages = getattr(obj, "recent_messages", None)
        if messages is None: messages = list(obj.messages.select_related("sender").order_by("-created_at")[:50])
        return AdminConversationMessageSerializer(list(reversed(messages)), many=True, context=self.context).data

class SubscriptionPlanSerializer(serializers.ModelSerializer):
    PAGE_CHOICES = {"homepage", "marketplace", "lost_found", "announcements", "jobs"}
    class Meta: model = SubscriptionPlan; fields = ("id", "name", "description", "price_kobo", "duration_days", "placement", "allowed_pages", "max_pages", "allowed_placements", "max_images", "visibility", "features", "is_popular", "color", "is_active", "created_at", "updated_at"); read_only_fields = ("id", "created_at", "updated_at")
    def validate_duration_days(self, value):
        if not 1 <= value <= 366: raise serializers.ValidationError("Duration must be between 1 and 366 days.")
        return value
    def validate_allowed_pages(self, value):
        if not isinstance(value, list) or not value or len(value) > len(self.PAGE_CHOICES):
            raise serializers.ValidationError("Choose between 1 and 5 advertising pages.")
        if any(not isinstance(page, str) or page not in self.PAGE_CHOICES for page in value):
            raise serializers.ValidationError("Choose valid advertising pages.")
        if len(set(value)) != len(value):
            raise serializers.ValidationError("Choose valid, unique advertising pages.")
        return value
    def validate_allowed_placements(self, value):
        if not isinstance(value, list) or any(not isinstance(key, str) for key in value):
            raise serializers.ValidationError("Choose valid advertising placements.")
        if len(value) != len(set(value)):
            raise serializers.ValidationError("Choose unique advertising placements.")
        valid = set(AdPlacement.objects.filter(is_active=True).values_list("key", flat=True))
        if any(not isinstance(key, str) or key not in valid for key in value):
            raise serializers.ValidationError("Choose active advertising placements.")
        return value
    def validate(self, attrs):
        pages = attrs.get("allowed_pages", getattr(self.instance, "allowed_pages", []))
        placements = attrs.get("allowed_placements", getattr(self.instance, "allowed_placements", []))
        max_pages = attrs.get("max_pages", getattr(self.instance, "max_pages", 1))
        if not pages:
            raise serializers.ValidationError({"allowed_pages": "Choose at least one page for this plan."})
        limit = len(placements) if placements else len(pages)
        if not 1 <= max_pages <= limit:
            raise serializers.ValidationError({"max_pages": "Maximum placements must be between 1 and the number of eligible placements."})
        return attrs

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
    whatsapp_phone_number_id = serializers.CharField(required=False, allow_blank=True, max_length=80)
    class Meta:
        model = PlatformSettings
        fields = ("site_name", "site_logo", "site_logo_url", "tagline", "support_email", "contact_email", "maintenance_mode", "maintenance_message", "marketplace_enabled", "lost_found_enabled", "jobs_enabled", "external_job_submissions_enabled", "job_listing_approval_required", "messaging_enabled", "ads_enabled", "homepage_ads_enabled", "marketplace_ads_enabled", "homepage_lost_found_enabled", "ad_slide_duration_seconds", "paystack_enabled", "listing_fee_enabled", "listing_fee_kobo", "sponsored_products_enabled", "sponsored_product_fee_enabled", "sponsored_product_fee_kobo", "sponsored_product_duration_days", "ad_fee_enabled", "ad_fee_kobo", "subscription_fee_enabled", "registration_enabled", "listing_approval_required", "max_product_images", "student_verification_enabled", "require_student_id", "email_verification_required", "auto_approve_students", "featured_products_enabled", "reviews_enabled", "review_moderation_enabled", "announcements_enabled", "email_notifications_enabled", "notification_channels", "whatsapp_phone_number_id", "report_notifications_enabled", "terms_text", "privacy_text", "community_guidelines_text", "updated_at")
        read_only_fields = ("updated_at",)
    def get_site_logo_url(self, obj):
        if not obj.site_logo: return ""
        request = self.context.get("request")
        return request.build_absolute_uri(obj.site_logo.url) if request else obj.site_logo.url
    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["whatsapp_phone_number_id"] = instance.whatsapp_phone_number_id or os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
        return data
    def validate_site_logo(self, image):
        if image and image.size > 2 * 1024 * 1024: raise serializers.ValidationError("The logo must be 2 MB or smaller.")
        if image and image.content_type not in ("image/jpeg", "image/png", "image/webp"): raise serializers.ValidationError("Use a PNG, JPG or WEBP logo.")
        return image
    def validate_max_product_images(self, value):
        if not 1 <= value <= 10:
            raise serializers.ValidationError("Maximum product images must be between 1 and 10.")
        return value
    def validate_ad_slide_duration_seconds(self, value):
        if not 5 <= value <= 86400:
            raise serializers.ValidationError("Ad slide duration must be between 5 seconds and 24 hours.")
        return value
    def validate_sponsored_product_duration_days(self, value):
        if not 1 <= value <= 366:
            raise serializers.ValidationError("Sponsored product duration must be between 1 and 366 days.")
        return value

    def validate_notification_channels(self, value):
        allowed = {"lost_found", "support", "official", "promotional", "account_security", "marketplace", "payments"}
        if not isinstance(value, dict) or set(value) - allowed:
            raise serializers.ValidationError("Choose valid notification categories.")
        for category, channels in value.items():
            if not isinstance(channels, dict) or set(channels) - {"email", "whatsapp"} or any(not isinstance(enabled, bool) for enabled in channels.values()):
                raise serializers.ValidationError({category: "Set email and WhatsApp options to on or off."})
        current = self.instance.notification_channels if self.instance else {}
        defaults = default_notification_channels()
        return {key: {**defaults[key], **current.get(key, {}), **value.get(key, {})} for key in defaults}

    def validate_whatsapp_phone_number_id(self, value):
        value = value.strip()
        if value and (not value.isdigit() or not 5 <= len(value) <= 80):
            raise serializers.ValidationError("Enter the numeric WhatsApp Business phone number ID from Meta, not the public phone number.")
        return value

class AdminPaymentSerializer(serializers.ModelSerializer):
    user_email = serializers.SerializerMethodField()
    product_title = serializers.CharField(source="product.title", read_only=True, allow_null=True)
    advertisement_title = serializers.CharField(source="advertisement.title", read_only=True, allow_null=True)
    subscription_plan_name = serializers.CharField(source="subscription_plan.name", read_only=True, allow_null=True)
    def get_user_email(self, obj):
        return obj.user.email if obj.user_id else obj.payer_email
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
        fields = ("id", "seller_id", "seller_name", "seller_email", "category", "category_name", "title", "description", "condition", "price", "location", "status", "is_featured", "featured_until", "sponsor_requested", "sponsorship_fee_kobo", "sponsorship_duration_days", "featured_position", "image_url", "created_at")
        read_only_fields = ("id", "seller_id", "seller_name", "seller_email", "category", "category_name", "title", "description", "condition", "price", "location", "sponsor_requested", "sponsorship_fee_kobo", "sponsorship_duration_days", "image_url", "created_at")
    def get_image_url(self, obj):
        if not obj.images.exists(): return None
        request = self.context.get("request")
        path = f"/api/admin-tools/products/{obj.pk}/image/"
        return request.build_absolute_uri(path) if request else path
    def get_seller_name(self, obj):
        return obj.seller.get_full_name().strip() or obj.seller.username

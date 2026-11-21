from django.utils import timezone
from rest_framework import serializers

from apps.accounts.models import User
from apps.messaging.models import Conversation
from .models import JobCategory, JobEngagement, JobListing, JobReport


class JobCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = JobCategory
        fields = ("id", "name", "slug", "category_type", "is_active")
        read_only_fields = ("id",)


class JobListingSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)
    poster_name = serializers.SerializerMethodField()
    poster_status = serializers.SerializerMethodField()
    is_owner = serializers.SerializerMethodField()
    is_sponsored = serializers.SerializerMethodField()
    poster_public_id = serializers.SerializerMethodField()
    identity_type = serializers.ChoiceField(choices=("normal", "anonymous"), required=False)

    class Meta:
        model = JobListing
        fields = (
            "id", "source", "kind", "engagement", "category", "category_name", "title",
            "description", "requirements", "pay_type", "pay_min", "pay_max", "location",
            "working_hours", "duration", "positions", "deadline", "application_instructions",
            "application_url", "business_name", "identity_type", "poster_public_id", "poster_name", "poster_status",
            "is_owner", "is_sponsored", "status", "admin_note", "created_at", "updated_at", "closed_at",
        )
        read_only_fields = ("id", "source", "status", "admin_note", "created_at", "updated_at", "closed_at", "business_name", "poster_public_id", "poster_name", "poster_status", "is_owner", "is_sponsored")

    def get_poster_name(self, obj):
        if obj.source == JobListing.Source.BUSINESS:
            return obj.business_name
        if obj.identity_type == "anonymous":
            return obj.anonymous_identity.handle if obj.anonymous_identity_id and obj.anonymous_identity.is_enabled else "Unavailable identity"
        if not obj.owner_id:
            return "Student"
        user = obj.owner
        if not user.public_name_enabled:
            return "Verified student"
        return user.get_full_name().strip() or user.username

    def get_poster_status(self, obj):
        if obj.source != JobListing.Source.STUDENT or not obj.owner_id:
            return "business"
        return obj.owner.student_status if obj.owner.is_student_verified else "student"

    def get_poster_public_id(self, obj):
        if obj.identity_type == "anonymous":
            return str(obj.anonymous_identity.public_id) if obj.anonymous_identity_id and obj.anonymous_identity.is_enabled else ""
        return str(obj.owner.public_id) if obj.owner_id else ""

    def get_is_owner(self, obj):
        request = self.context.get("request")
        return bool(request and request.user.is_authenticated and obj.owner_id == request.user.pk)

    def get_is_sponsored(self, obj):
        return False

    def validate_category(self, value):
        if not value.is_active:
            raise serializers.ValidationError("Choose an active category.")
        return value

    def validate_application_url(self, value):
        if value and not value.lower().startswith("https://"):
            raise serializers.ValidationError("Application links must use HTTPS.")
        return value

    def validate_deadline(self, value):
        today = timezone.localdate()
        if value <= today:
            raise serializers.ValidationError("Choose a future application deadline.")
        if value > today + timezone.timedelta(days=180):
            raise serializers.ValidationError("Application deadlines can be no more than 180 days away.")
        return value

    def validate(self, attrs):
        category = attrs.get("category", getattr(self.instance, "category", None))
        kind = attrs.get("kind", getattr(self.instance, "kind", JobListing.Kind.JOB))
        if category and category.category_type != ("service" if kind == JobListing.Kind.SERVICE else "job"):
            raise serializers.ValidationError({"category": "Choose a category that matches this listing type."})
        pay_min = attrs.get("pay_min", getattr(self.instance, "pay_min", None))
        pay_max = attrs.get("pay_max", getattr(self.instance, "pay_max", None))
        if pay_min is not None and pay_min < 0 or pay_max is not None and pay_max < 0:
            raise serializers.ValidationError("Pay amounts cannot be negative.")
        if pay_min is not None and pay_max is not None and pay_max < pay_min:
            raise serializers.ValidationError({"pay_max": "Maximum pay must be greater than or equal to minimum pay."})
        identity_type = attrs.get("identity_type", getattr(self.instance, "identity_type", "normal"))
        request = self.context.get("request")
        if identity_type == "anonymous":
            alias = getattr(getattr(request, "user", None), "anonymous_marketplace_identity", None)
            if not alias or not alias.is_enabled:
                raise serializers.ValidationError({"identity_type": "Create and enable your anonymous marketplace identity first."})
            attrs["_anonymous_identity"] = alias
        else:
            attrs["_anonymous_identity"] = None
        return attrs


class JobEngagementSerializer(serializers.ModelSerializer):
    listing_title = serializers.CharField(source="listing.title", read_only=True)
    listing_id = serializers.IntegerField(source="listing_id", read_only=True)
    conversation = serializers.PrimaryKeyRelatedField(queryset=Conversation.objects.all(), write_only=True, required=False)
    conversation_id = serializers.IntegerField(read_only=True)
    is_poster = serializers.SerializerMethodField()
    initiated_by_me = serializers.SerializerMethodField()
    waiting_for_me = serializers.SerializerMethodField()

    class Meta:
        model = JobEngagement
        fields = ("id", "listing_id", "listing_title", "conversation", "conversation_id", "is_poster", "initiated_by_me", "waiting_for_me", "status", "accepted_at", "completed_at", "created_at", "updated_at")
        read_only_fields = tuple(field for field in fields if field != "conversation")

    def get_initiated_by_me(self, obj):
        request = self.context.get("request")
        return bool(request and request.user.pk == obj.initiated_by_id)

    def get_is_poster(self, obj):
        request = self.context.get("request")
        return bool(request and request.user.pk == obj.poster_id)

    def get_waiting_for_me(self, obj):
        request = self.context.get("request")
        if not request:
            return False
        if obj.status == JobEngagement.Status.OFFERED:
            return request.user.pk != obj.initiated_by_id
        if obj.status == JobEngagement.Status.COMPLETION_REQUESTED:
            return request.user.pk != obj.completion_requested_by_id
        return False


class ExternalJobSubmissionSerializer(serializers.ModelSerializer):
    business_name = serializers.CharField(max_length=180)
    business_contact_name = serializers.CharField(max_length=120)
    business_email = serializers.EmailField()
    business_phone = serializers.CharField(max_length=30)
    business_website = serializers.URLField(required=False, allow_blank=True)
    business_description = serializers.CharField(required=False, allow_blank=True, max_length=1500)

    class Meta:
        model = JobListing
        fields = (
            "kind", "engagement", "category", "title", "description", "requirements", "pay_type",
            "pay_min", "pay_max", "location", "working_hours", "duration", "positions", "deadline",
            "application_instructions", "application_url", "business_name", "business_contact_name",
            "business_email", "business_phone", "business_website", "business_description",
        )

    def validate_category(self, value):
        if not value.is_active:
            raise serializers.ValidationError("Choose an active category.")
        return value

    def validate_application_url(self, value):
        if value and not value.lower().startswith("https://"):
            raise serializers.ValidationError("Application links must use HTTPS.")
        return value

    def validate_deadline(self, value):
        today = timezone.localdate()
        if value <= today or value > today + timezone.timedelta(days=180):
            raise serializers.ValidationError("Choose a deadline within the next 180 days.")
        return value

    def validate(self, attrs):
        if attrs["category"].category_type != ("service" if attrs["kind"] == JobListing.Kind.SERVICE else "job"):
            raise serializers.ValidationError({"category": "Choose a category that matches this opportunity type."})
        pay_min, pay_max = attrs.get("pay_min"), attrs.get("pay_max")
        if (pay_min is not None and pay_min < 0) or (pay_max is not None and pay_max < 0):
            raise serializers.ValidationError("Pay amounts cannot be negative.")
        if pay_min is not None and pay_max is not None and pay_max < pay_min:
            raise serializers.ValidationError({"pay_max": "Maximum pay must be greater than or equal to minimum pay."})
        return attrs


class JobReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = JobReport
        fields = ("id", "listing", "reason", "details", "status", "created_at")
        read_only_fields = ("id", "status", "created_at")

    def validate_listing(self, listing):
        if listing.status != JobListing.Status.ACTIVE or listing.deadline < timezone.localdate():
            raise serializers.ValidationError("You can report an active listing only.")
        return listing

    def validate(self, attrs):
        request = self.context["request"]
        if attrs["listing"].owner_id == request.user.pk:
            raise serializers.ValidationError("You cannot report your own listing.")
        if JobReport.objects.filter(listing=attrs["listing"], reporter=request.user, status__in=(JobReport.Status.OPEN, JobReport.Status.REVIEWING)).exists():
            raise serializers.ValidationError("You already have an open report for this listing.")
        return attrs


class AdminJobListingSerializer(serializers.ModelSerializer):
    owner_email = serializers.EmailField(source="owner.email", read_only=True, allow_null=True)
    category_name = serializers.CharField(source="category.name", read_only=True)
    anonymous_identity_name = serializers.CharField(source="anonymous_identity.handle", read_only=True, allow_null=True)

    class Meta:
        model = JobListing
        fields = (
            "id", "source", "owner", "owner_email", "identity_type", "anonymous_identity", "anonymous_identity_name", "kind", "engagement", "category", "category_name",
            "title", "description", "requirements", "pay_type", "pay_min", "pay_max", "location",
            "working_hours", "duration", "positions", "deadline", "application_instructions", "application_url",
            "business_name", "business_contact_name", "business_email", "business_phone", "business_website",
            "business_description", "business_email_verified_at", "status", "admin_note", "reviewed_by",
            "reviewed_at", "created_at", "updated_at", "closed_at", "management_token_expires_at",
            "management_token_revoked_at", "management_last_used_at",
        )
        read_only_fields = (
            "id", "source", "owner", "owner_email", "anonymous_identity_name", "category_name", "business_email_verified_at", "status",
            "reviewed_by", "reviewed_at", "created_at", "updated_at", "closed_at", "management_token_expires_at",
            "management_token_revoked_at", "management_last_used_at",
        )


class AdminJobReportSerializer(serializers.ModelSerializer):
    reporter_email = serializers.EmailField(source="reporter.email", read_only=True)
    listing_title = serializers.CharField(source="listing.title", read_only=True)

    class Meta:
        model = JobReport
        fields = "__all__"
        read_only_fields = ("created_at", "reporter", "listing")

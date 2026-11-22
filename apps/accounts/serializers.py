import os
import re
import json
from rest_framework import serializers
from django.contrib.auth.password_validation import validate_password
from django.utils import timezone
from .models import User, StudentVerification, AccountActionToken, ProfileChangeRequest, ProfileChangeMessage, CourseProgram, AnonymousMarketplaceIdentity


def normalize_whatsapp_number(value):
    number = re.sub(r"[\s()\-]", "", str(value or "").strip())
    digits = number.lstrip("+")
    if re.fullmatch(r"0\d{10}", digits):
        return "+234" + digits[1:]
    if re.fullmatch(r"234\d{10}", digits):
        return "+" + digits
    return number

class CourseProgramSerializer(serializers.ModelSerializer):
    class Meta:
        model = CourseProgram
        fields = ("id", "name", "duration_years", "is_active")
        read_only_fields = ("id",)

    def validate_name(self, value):
        return value.strip()

    def validate_duration_years(self, value):
        if not 1 <= value <= 10:
            raise serializers.ValidationError("Course duration must be between 1 and 10 years.")
        return value

class UserSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="public_id", read_only=True)
    profile_picture_url = serializers.SerializerMethodField()
    can_access_student_features = serializers.SerializerMethodField()
    course_name = serializers.CharField(source="course.name", read_only=True, allow_null=True)
    student_status = serializers.CharField(read_only=True)
    class Meta:
        model = User
        fields = ("id", "public_id", "username", "first_name", "last_name", "email", "email_verified", "date_joined", "last_seen_at", "matric_number", "department", "faculty", "level", "course", "course_name", "entry_year", "student_status", "phone_number", "whatsapp_number", "campus", "bio", "profile_picture_url", "role", "is_staff", "is_superuser", "is_student_verified", "can_access_student_features", "notification_email_enabled", "notification_whatsapp_enabled", "public_profile_picture_enabled")
        read_only_fields = fields
    def get_profile_picture_url(self, obj):
        if not obj.profile_picture:
            return None
        return f"/api/accounts/profile-image/{obj.public_id}/"

    def get_can_access_student_features(self, obj):
        from .permissions import is_student_feature_eligible
        return is_student_feature_eligible(obj)

class StudentPublicProfileSerializer(serializers.ModelSerializer):
    profile_picture_url = serializers.SerializerMethodField()
    course_name = serializers.CharField(source="course.name", read_only=True, allow_null=True)
    student_status = serializers.CharField(read_only=True)
    class Meta:
        model = User
        fields = ("public_id", "username", "first_name", "last_name", "department", "faculty", "level", "course_name", "student_status", "entry_year", "is_student_verified", "last_seen_at", "profile_picture_url", "public_name_enabled", "public_department_enabled", "public_level_enabled")
        read_only_fields = fields
    def get_profile_picture_url(self, obj):
        return f"/api/accounts/profile-image/{obj.public_id}/" if obj.profile_picture and obj.public_profile_enabled and obj.public_profile_picture_enabled else None

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data.pop("public_name_enabled", None)
        data.pop("public_department_enabled", None)
        data.pop("public_level_enabled", None)
        if not instance.public_name_enabled:
            data["first_name"] = ""
            data["last_name"] = ""
            data["username"] = "Student"
        if not instance.public_department_enabled:
            data["department"] = ""
            data["faculty"] = ""
        if not instance.public_level_enabled:
            data["level"] = ""
            data["course_name"] = None
            data["entry_year"] = None
        return data


class AnonymousMarketplaceIdentitySerializer(serializers.ModelSerializer):
    class Meta:
        model = AnonymousMarketplaceIdentity
        fields = ("public_id", "handle", "is_enabled", "created_at")
        read_only_fields = ("public_id", "handle", "created_at")
    def create(self, validated_data):
        import secrets
        user = self.context["request"].user
        if hasattr(user, "anonymous_marketplace_identity"):
            raise serializers.ValidationError("You already have an anonymous marketplace identity.")
        requested_handle = validated_data.pop("handle", None)
        if requested_handle:
            return AnonymousMarketplaceIdentity.objects.create(user=user, handle=requested_handle, **validated_data)
        for _ in range(5):
            handle = f"User{secrets.randbelow(900000) + 100000}"
            if not AnonymousMarketplaceIdentity.objects.filter(handle=handle).exists():
                return AnonymousMarketplaceIdentity.objects.create(user=user, handle=handle, **validated_data)
        raise serializers.ValidationError("Could not create a unique marketplace identity. Try again.")

class RegisterSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="public_id", read_only=True)
    password=serializers.CharField(write_only=True, min_length=8)
    matric_number=serializers.CharField(required=True, max_length=40)
    phone_number=serializers.CharField(required=True, max_length=25)
    course=serializers.PrimaryKeyRelatedField(queryset=CourseProgram.objects.filter(is_active=True), required=True)
    entry_year=serializers.IntegerField(required=True, min_value=2000)
    class Meta: model=User; fields=("id","public_id","username","first_name","last_name","email","matric_number","phone_number","password","department","faculty","level","course","entry_year"); read_only_fields=("id","public_id")
    def validate_email(self, value):
        value = value.strip().lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("An account already uses this email address. Sign in or contact the school administrator.")
        return value
    def validate_username(self, value):
        value = value.strip()
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("This username is already registered. Sign in to your existing account.")
        return value
    def validate_matric_number(self, value):
        value = value.strip().upper()
        if not value:
            raise serializers.ValidationError("Enter your matriculation number.")
        if User.objects.filter(matric_number__iexact=value).exists() or StudentVerification.objects.filter(submitted_matric_number__iexact=value).exists():
            raise serializers.ValidationError("This matriculation number is already linked to an account. Sign in or contact the school administrator.")
        return value
    def validate_phone_number(self, value):
        value = value.strip()
        if sum(character.isdigit() for character in value) < 7:
            raise serializers.ValidationError("Enter a valid phone number with at least 7 digits.")
        return value
    def validate_password(self, value):
        validate_password(value)
        return value
    def validate_entry_year(self, value):
        from django.utils import timezone
        if value > timezone.localdate().year:
            raise serializers.ValidationError("Entry year cannot be in the future.")
        if value < timezone.localdate().year - 12:
            raise serializers.ValidationError("Check the entry year; it must be within the last 12 years.")
        return value
    def create(self, validated_data):
        user = User.objects.create_user(**validated_data)
        from apps.administration.models import PlatformSettings
        if PlatformSettings.get_solo().email_verification_required:
            from .services import issue_account_action_token
            issue_account_action_token(user, AccountActionToken.Purpose.EMAIL_VERIFY)
        else:
            user.email_verified = True
            user.save(update_fields=("email_verified",))
        return user

class ProfileUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("username", "first_name", "last_name", "department", "faculty", "level", "campus", "bio", "phone_number", "whatsapp_number", "profile_picture", "notification_email_enabled", "notification_whatsapp_enabled", "public_name_enabled", "public_department_enabled", "public_level_enabled", "public_profile_enabled", "public_profile_picture_enabled")

    def validate_profile_picture(self, image):
        if image and image.size > 5 * 1024 * 1024:
            raise serializers.ValidationError("Profile pictures must be 5 MB or smaller.")
        return image

    def validate_username(self, value):
        value = value.strip()
        if User.objects.filter(username__iexact=value).exclude(pk=self.instance.pk).exists():
            raise serializers.ValidationError("This username is already in use.")
        return value

    def validate(self, attrs):
        whatsapp_enabled = attrs.get("notification_whatsapp_enabled", self.instance.notification_whatsapp_enabled)
        number = attrs.get("whatsapp_number", self.instance.whatsapp_number)
        if number and not re.fullmatch(r"\+[1-9]\d{7,14}", number):
            raise serializers.ValidationError({"whatsapp_number": "Use international E.164 format, such as +2348012345678."})
        if whatsapp_enabled and not number:
            raise serializers.ValidationError({"whatsapp_number": "Add a WhatsApp number before enabling WhatsApp notifications."})
        from apps.administration.models import PlatformSettings
        whatsapp_sender = PlatformSettings.get_solo().whatsapp_phone_number_id or os.getenv("WHATSAPP_PHONE_NUMBER_ID")
        any_template = any(os.getenv(key) for key in ("WHATSAPP_MATCH_TEMPLATE", "WHATSAPP_SUPPORT_TEMPLATE", "WHATSAPP_OFFICIAL_TEMPLATE", "WHATSAPP_PROMOTION_TEMPLATE", "WHATSAPP_SECURITY_TEMPLATE", "WHATSAPP_ORDER_TEMPLATE", "WHATSAPP_LISTING_TEMPLATE", "WHATSAPP_AD_TEMPLATE"))
        if attrs.get("notification_whatsapp_enabled") is True and not all((os.getenv("WHATSAPP_ACCESS_TOKEN"), whatsapp_sender, any_template)):
            raise serializers.ValidationError({"notification_whatsapp_enabled": "WhatsApp notifications are not configured by the school yet."})
        return attrs

    def update(self, instance, validated_data):
        enabling = validated_data.get("notification_whatsapp_enabled") is True and (not instance.notification_whatsapp_enabled or not instance.whatsapp_consent_at)
        disabling = validated_data.get("notification_whatsapp_enabled") is False
        if enabling:
            instance.whatsapp_consent_at = timezone.now()
        elif disabling:
            instance.whatsapp_consent_at = None
        return super().update(instance, validated_data)
class StudentVerificationSerializer(serializers.ModelSerializer):
    student_id_image = serializers.ImageField(write_only=True, required=False, allow_null=True)
    profile_picture = serializers.ImageField(write_only=True, required=False, allow_null=True)
    profile_photo_consent = serializers.BooleanField(write_only=True, required=False)
    class Meta: model=StudentVerification; fields=("id","student_id_image","profile_picture","profile_photo_consent","submitted_matric_number","requested_affiliation","status","reviewer_note","created_at"); read_only_fields=("status","reviewer_note","created_at")

    def validate(self, attrs):
        user = self.context["request"].user
        from apps.administration.models import PlatformSettings
        platform = PlatformSettings.get_solo()
        if not platform.student_verification_enabled: raise serializers.ValidationError("Student verification is currently disabled by the school.")
        if platform.require_student_id and not attrs.get("student_id_image") and self.instance is None: raise serializers.ValidationError({"student_id_image": "Upload your student ID card or alumni proof to continue."})
        if self.instance is None and not attrs.get("profile_picture") and not user.profile_picture:
            raise serializers.ValidationError({"profile_picture": "Upload a clear photo of yourself for account security."})
        if self.instance is None and not attrs.get("profile_photo_consent"):
            raise serializers.ValidationError({"profile_photo_consent": "Confirm that you agree to Tarmac storing your profile photo for account security."})
        if self.instance is None and user.is_student_verified:
            raise serializers.ValidationError("Your student account is already verified.")
        if self.instance and self.instance.status != StudentVerification.Status.PENDING:
            raise serializers.ValidationError("A reviewed verification submission cannot be changed.")
        if self.instance and "profile_picture" in attrs:
            raise serializers.ValidationError({"profile_picture": "Use the profile change request to update your saved profile photo."})
        if self.instance is None and StudentVerification.objects.filter(user=user, status=StudentVerification.Status.PENDING).exists():
            raise serializers.ValidationError("You already have a pending verification submission.")
        return attrs

    def validate_student_id_image(self, image):
        if image and image.size > 5 * 1024 * 1024:
            raise serializers.ValidationError("Student ID images must be 5 MB or smaller.")
        return image
    def validate_profile_picture(self, image):
        if image and image.size > 5 * 1024 * 1024:
            raise serializers.ValidationError("Your profile photo must be 5 MB or smaller.")
        return image
    def validate_submitted_matric_number(self, value):
        value = value.strip().upper()
        current_user = self.context["request"].user
        if current_user.matric_number and current_user.matric_number.casefold() != value.casefold():
            has_previous_rejection = StudentVerification.objects.filter(
                user=current_user,
                status=StudentVerification.Status.REJECTED,
            ).exists()
            if current_user.is_student_verified or not has_previous_rejection:
                raise serializers.ValidationError("This must match the matriculation number used to create your account. Contact an administrator if it needs correction.")
        duplicates = StudentVerification.objects.filter(submitted_matric_number__iexact=value).exclude(user=current_user)
        if duplicates.exists():
            raise serializers.ValidationError("This matric number is already linked to another account. Contact the school administrator if this is an error.")
        if User.objects.filter(matric_number__iexact=value).exclude(pk=current_user.pk).exists():
            raise serializers.ValidationError("This matric number is already linked to another account.")
        return value


class ProfileChangeMessageSerializer(serializers.ModelSerializer):
    sender_name = serializers.SerializerMethodField()
    sender_is_admin = serializers.BooleanField(source="sender.is_staff", read_only=True)
    class Meta:
        model = ProfileChangeMessage
        fields = ("id", "sender_name", "sender_is_admin", "body", "created_at")
    def get_sender_name(self, obj):
        return obj.sender.get_full_name().strip() or obj.sender.username


class StudentProfileChangeRequestSerializer(serializers.ModelSerializer):
    changes = serializers.CharField(write_only=True, required=False, allow_blank=True)
    profile_picture = serializers.ImageField(write_only=True, required=False)
    requested_profile_picture_url = serializers.SerializerMethodField()
    messages = ProfileChangeMessageSerializer(many=True, read_only=True)
    class Meta:
        model = ProfileChangeRequest
        fields = ("id", "changes", "profile_picture", "requested_changes", "requested_profile_picture_url", "status", "messages", "created_at", "updated_at")
        read_only_fields = ("requested_changes", "status", "messages", "created_at", "updated_at")

    def validate_profile_picture(self, image):
        if image.size > 5 * 1024 * 1024:
            raise serializers.ValidationError("Profile pictures must be 5 MB or smaller.")
        return image

    def validate_changes(self, value):
        if not value:
            return {}
        try:
            changes = json.loads(value)
        except (TypeError, ValueError):
            raise serializers.ValidationError("Profile changes must be valid JSON.")
        if not isinstance(changes, dict):
            raise serializers.ValidationError("Profile changes must be an object.")
        allowed = {"email", "matric_number", "username", "first_name", "last_name", "department", "faculty", "level", "campus", "bio", "phone_number", "whatsapp_number"}
        if set(changes) - allowed:
            raise serializers.ValidationError("Choose valid profile fields to change.")
        user = self.context["request"].user
        new_email = changes.pop("email", None)
        new_matric = changes.pop("matric_number", None)
        if new_email is not None:
            try:
                new_email = serializers.EmailField().run_validation(new_email).strip().lower()
            except serializers.ValidationError as exc:
                raise serializers.ValidationError({"email": exc.detail})
            if User.objects.filter(email__iexact=new_email).exclude(pk=user.pk).exists():
                raise serializers.ValidationError({"email": "An account already uses this email address."})
            if new_email.casefold() == user.email.casefold():
                new_email = None
        if new_matric is not None:
            new_matric = str(new_matric).strip().upper()
            if not new_matric or len(new_matric) > 40:
                raise serializers.ValidationError({"matric_number": "Enter a valid matriculation number."})
            if User.objects.filter(matric_number__iexact=new_matric).exclude(pk=user.pk).exists() or StudentVerification.objects.filter(submitted_matric_number__iexact=new_matric).exclude(user=user).exists():
                raise serializers.ValidationError({"matric_number": "This matriculation number is already linked to another account."})
            if user.matric_number and new_matric.casefold() == user.matric_number.casefold():
                new_matric = None
        if "whatsapp_number" in changes:
            changes["whatsapp_number"] = normalize_whatsapp_number(changes["whatsapp_number"])
        unchanged = set()
        for key, value in changes.items():
            current_value = getattr(user, key, "")
            if key == "whatsapp_number":
                current_value = normalize_whatsapp_number(current_value)
            if str(value).strip() == str(current_value).strip():
                unchanged.add(key)
        changes = {key: value for key, value in changes.items() if key not in unchanged}
        if not changes:
            special_changes = {}
            if new_email is not None: special_changes["email"] = new_email
            if new_matric is not None: special_changes["matric_number"] = new_matric
            return special_changes
        validator = ProfileUpdateSerializer(user, data=changes, partial=True)
        validator.is_valid(raise_exception=True)
        validated = dict(validator.validated_data)
        if new_email is not None:
            validated["email"] = new_email
        if new_matric is not None:
            validated["matric_number"] = new_matric
        return validated

    def validate(self, attrs):
        if not attrs.get("changes") and not attrs.get("profile_picture"):
            raise serializers.ValidationError("Describe at least one profile change.")
        if ProfileChangeRequest.objects.filter(user=self.context["request"].user, status__in=(ProfileChangeRequest.Status.PENDING, ProfileChangeRequest.Status.NEEDS_INFO)).exists():
            raise serializers.ValidationError("You already have a profile change request waiting for review.")
        return attrs

    def create(self, validated_data):
        changes = validated_data.pop("changes", {})
        picture = validated_data.pop("profile_picture", None)
        return ProfileChangeRequest.objects.create(user=self.context["request"].user, requested_changes=changes, requested_profile_picture=picture)

    def get_requested_profile_picture_url(self, obj):
        if not obj.requested_profile_picture:
            return None
        request = self.context.get("request")
        path = f"/api/accounts/profile-change-requests/{obj.pk}/image/"
        return request.build_absolute_uri(path) if request else path


class AdminProfileChangeRequestSerializer(serializers.ModelSerializer):
    user_name = serializers.SerializerMethodField()
    user_email = serializers.EmailField(source="user.email", read_only=True)
    requested_profile_picture_url = serializers.SerializerMethodField()
    messages = ProfileChangeMessageSerializer(many=True, read_only=True)
    class Meta:
        model = ProfileChangeRequest
        fields = ("id", "user", "user_name", "user_email", "requested_changes", "requested_profile_picture_url", "status", "messages", "reviewed_by", "reviewed_at", "created_at", "updated_at")
        read_only_fields = ("id", "user", "user_name", "user_email", "requested_changes", "requested_profile_picture_url", "messages", "reviewed_by", "reviewed_at", "created_at", "updated_at")
    def get_user_name(self, obj):
        return obj.user.get_full_name().strip() or obj.user.username
    def get_requested_profile_picture_url(self, obj):
        if not obj.requested_profile_picture:
            return None
        request = self.context.get("request")
        path = f"/api/admin-tools/profile-change-requests/{obj.pk}/image/"
        return request.build_absolute_uri(path) if request else path

    def validate_status(self, value):
        if value not in (ProfileChangeRequest.Status.APPROVED, ProfileChangeRequest.Status.REJECTED):
            raise serializers.ValidationError("Choose approved or rejected. Use the question action to request more information.")
        return value

class EmailTokenSerializer(serializers.Serializer):
    email = serializers.EmailField()
    token = serializers.CharField(max_length=128)

class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()

class PasswordResetConfirmSerializer(EmailTokenSerializer):
    new_password = serializers.CharField(write_only=True, min_length=8)

    def validate_new_password(self, value):
        user = User.objects.filter(email__iexact=self.initial_data.get("email", "")).first()
        validate_password(value, user=user)
        return value

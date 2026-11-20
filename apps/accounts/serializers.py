import os
import re
import json
from rest_framework import serializers
from django.contrib.auth.password_validation import validate_password
from django.utils import timezone
from .models import User, StudentVerification, AccountActionToken, ProfileChangeRequest, ProfileChangeMessage
class UserSerializer(serializers.ModelSerializer):
    profile_picture_url = serializers.SerializerMethodField()
    can_access_student_features = serializers.SerializerMethodField()
    class Meta:
        model = User
        fields = ("id", "username", "first_name", "last_name", "email", "email_verified", "date_joined", "matric_number", "department", "faculty", "level", "phone_number", "whatsapp_number", "campus", "bio", "profile_picture_url", "role", "is_staff", "is_superuser", "is_student_verified", "can_access_student_features", "notification_email_enabled", "notification_whatsapp_enabled")
        read_only_fields = fields
    def get_profile_picture_url(self, obj):
        if not obj.profile_picture:
            return None
        return f"/api/accounts/profile-image/{obj.pk}/"

    def get_can_access_student_features(self, obj):
        from .permissions import is_student_feature_eligible
        return is_student_feature_eligible(obj)

class StudentPublicProfileSerializer(serializers.ModelSerializer):
    profile_picture_url = serializers.SerializerMethodField()
    class Meta:
        model = User
        fields = ("id", "username", "first_name", "last_name", "department", "faculty", "level", "campus", "bio", "is_student_verified", "profile_picture_url")
    def get_profile_picture_url(self, obj):
        return f"/api/accounts/profile-image/{obj.pk}/" if obj.profile_picture else None
class RegisterSerializer(serializers.ModelSerializer):
    password=serializers.CharField(write_only=True, min_length=8)
    matric_number=serializers.CharField(required=True, max_length=40)
    class Meta: model=User; fields=("id","username","first_name","last_name","email","matric_number","password","department","faculty","level")
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
    def validate_password(self, value):
        validate_password(value)
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
        fields = ("username", "first_name", "last_name", "department", "faculty", "level", "campus", "bio", "phone_number", "whatsapp_number", "profile_picture", "notification_email_enabled", "notification_whatsapp_enabled")

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
        if attrs.get("notification_whatsapp_enabled") is True and not all((os.getenv("WHATSAPP_ACCESS_TOKEN"), os.getenv("WHATSAPP_PHONE_NUMBER_ID"), os.getenv("WHATSAPP_MATCH_TEMPLATE"))):
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
    class Meta: model=StudentVerification; fields=("id","student_id_image","submitted_matric_number","status","created_at"); read_only_fields=("status","created_at")

    def validate(self, attrs):
        user = self.context["request"].user
        from apps.administration.models import PlatformSettings
        platform = PlatformSettings.get_solo()
        if not platform.student_verification_enabled: raise serializers.ValidationError("Student verification is currently disabled by the school.")
        if platform.require_student_id and not attrs.get("student_id_image") and self.instance is None: raise serializers.ValidationError({"student_id_image": "Upload your student ID card to continue."})
        if self.instance is None and user.is_student_verified:
            raise serializers.ValidationError("Your student account is already verified.")
        if self.instance and self.instance.status != StudentVerification.Status.PENDING:
            raise serializers.ValidationError("A reviewed verification submission cannot be changed.")
        if self.instance is None and StudentVerification.objects.filter(user=user, status=StudentVerification.Status.PENDING).exists():
            raise serializers.ValidationError("You already have a pending verification submission.")
        return attrs

    def validate_student_id_image(self, image):
        if image.size > 5 * 1024 * 1024:
            raise serializers.ValidationError("Student ID images must be 5 MB or smaller.")
        return image
    def validate_submitted_matric_number(self, value):
        value = value.strip().upper()
        current_user = self.context["request"].user
        if current_user.matric_number and current_user.matric_number.casefold() != value.casefold():
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
        changes = {key: value for key, value in changes.items() if str(value).strip() != str(getattr(user, key, "")).strip()}
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

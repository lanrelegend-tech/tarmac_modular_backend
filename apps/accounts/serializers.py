import os
import re
from rest_framework import serializers
from django.contrib.auth.password_validation import validate_password
from django.utils import timezone
from .models import User, StudentVerification, AccountActionToken
class UserSerializer(serializers.ModelSerializer):
    profile_picture_url = serializers.SerializerMethodField()
    class Meta:
        model = User
        fields = ("id", "username", "first_name", "last_name", "email", "email_verified", "matric_number", "department", "faculty", "level", "phone_number", "whatsapp_number", "profile_picture_url", "role", "is_staff", "is_student_verified", "notification_email_enabled", "notification_whatsapp_enabled")
        read_only_fields = fields
    def get_profile_picture_url(self, obj):
        if not obj.profile_picture:
            return None
        return f"/api/accounts/profile-image/{obj.pk}/"

class StudentPublicProfileSerializer(serializers.ModelSerializer):
    profile_picture_url = serializers.SerializerMethodField()
    class Meta:
        model = User
        fields = ("id", "username", "first_name", "last_name", "department", "faculty", "level", "is_student_verified", "profile_picture_url")
    def get_profile_picture_url(self, obj):
        return f"/api/accounts/profile-image/{obj.pk}/" if obj.profile_picture else None
class RegisterSerializer(serializers.ModelSerializer):
    password=serializers.CharField(write_only=True, min_length=8)
    class Meta: model=User; fields=("id","username","first_name","last_name","email","password","department","faculty","level")
    def validate_email(self, value):
        value = value.strip().lower()
        allowed_domains = [domain.strip().lower() for domain in os.getenv("SCHOOL_EMAIL_DOMAINS", "").split(",") if domain.strip()]
        if allowed_domains and value.rsplit("@", 1)[-1] not in allowed_domains:
            raise serializers.ValidationError("Use your school email address to register.")
        return value
    def validate_matric_number(self, value):
        return value.strip().upper() if value else value
    def validate_password(self, value):
        validate_password(value)
        return value
    def create(self, validated_data):
        user = User.objects.create_user(**validated_data)
        from .services import issue_account_action_token
        issue_account_action_token(user, AccountActionToken.Purpose.EMAIL_VERIFY)
        return user

class ProfileUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("first_name", "last_name", "department", "faculty", "level", "phone_number", "whatsapp_number", "profile_picture", "notification_email_enabled", "notification_whatsapp_enabled")

    def validate_profile_picture(self, image):
        if image and image.size > 5 * 1024 * 1024:
            raise serializers.ValidationError("Profile pictures must be 5 MB or smaller.")
        return image

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
    student_id_image = serializers.ImageField(write_only=True)
    class Meta: model=StudentVerification; fields=("id","student_id_image","submitted_matric_number","status","created_at"); read_only_fields=("status","created_at")

    def validate(self, attrs):
        user = self.context["request"].user
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
        duplicates = StudentVerification.objects.filter(submitted_matric_number__iexact=value, status__in=(StudentVerification.Status.PENDING, StudentVerification.Status.APPROVED))
        if self.instance:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        if duplicates.exists():
            raise serializers.ValidationError("This matric number already has an active or approved verification request.")
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

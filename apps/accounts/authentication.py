from django.contrib.auth import get_user_model
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer, TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken, TokenError
from apps.administration.models import PlatformSettings

User = get_user_model()


class SchoolJWTAuthentication(JWTAuthentication):
    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        if not user.is_active or user.is_suspended:
            raise AuthenticationFailed("This account is inactive or suspended.", code="account_suspended")
        return user


class SchoolTokenObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        data = super().validate(attrs)
        if PlatformSettings.get_solo().email_verification_required and not self.user.email_verified and not self.user.is_staff:
            raise AuthenticationFailed("Verify your email address before signing in.")
        if not self.user.is_active or self.user.is_suspended:
            raise AuthenticationFailed("This account is inactive or suspended.")
        return data


class SchoolTokenRefreshSerializer(TokenRefreshSerializer):
    def validate(self, attrs):
        try:
            token = RefreshToken(attrs["refresh"])
            user = User.objects.get(pk=token["user_id"])
        except (KeyError, User.DoesNotExist, TokenError):
            raise AuthenticationFailed("This account is unavailable.")
        if not user.is_active or user.is_suspended:
            raise AuthenticationFailed("This account is inactive or suspended.")
        if PlatformSettings.get_solo().email_verification_required and not user.email_verified and not user.is_staff:
            raise AuthenticationFailed("Verify your email address before signing in.")
        return super().validate(attrs)

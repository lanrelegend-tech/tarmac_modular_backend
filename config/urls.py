from django.contrib import admin
from django.contrib.admin import AdminSite
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.middleware.csrf import get_token
import hashlib
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.throttling import AnonRateThrottle
from apps.payments.webhooks import paystack_webhook
from apps.accounts.authentication import (
    SchoolJWTAuthentication,
    SchoolTokenObtainPairSerializer,
    SchoolTokenRefreshSerializer,
)
from rest_framework_simplejwt.tokens import RefreshToken, TokenError
from apps.administration.views import system_status, system_logo, PublicEmergencyContactListView
from config.health import live as liveness_probe, ready as readiness_probe


class TarmacAdminSite(AdminSite):
    def has_permission(self, request):
        user = request.user
        return bool(user.is_active and user.is_staff and (user.is_superuser or user.role == "admin"))


# Keep moderators out of Django's separate built-in admin interface.
admin.site.__class__ = TarmacAdminSite

def _set_auth_cookies(response, access, refresh=None):
    response.set_cookie(
        settings.AUTH_ACCESS_COOKIE,
        access,
        max_age=int(settings.SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"].total_seconds()),
        httponly=True,
        secure=settings.AUTH_COOKIE_SECURE,
        samesite=settings.AUTH_COOKIE_SAMESITE,
        path="/",
    )
    if refresh:
        response.set_cookie(
            settings.AUTH_REFRESH_COOKIE,
            refresh,
            max_age=int(settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"].total_seconds()),
            httponly=True,
            secure=settings.AUTH_COOKIE_SECURE,
            samesite=settings.AUTH_COOKIE_SAMESITE,
            path="/api/auth/",
        )


def _clear_auth_cookies(response):
    response.delete_cookie(settings.AUTH_ACCESS_COOKIE, path="/", samesite=settings.AUTH_COOKIE_SAMESITE)
    response.delete_cookie(settings.AUTH_REFRESH_COOKIE, path="/api/auth/", samesite=settings.AUTH_COOKIE_SAMESITE)


class CsrfTokenView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        response = Response({"csrfToken": get_token(request._request)})
        response["Cache-Control"] = "no-store"
        return response


class LoginThrottle(AnonRateThrottle):
    scope = "auth_login"

    def get_cache_key(self, request, view):
        identity = self.get_ident(request)
        email = str(request.data.get("email", "")).strip().casefold()
        digest = hashlib.sha256(f"{identity}:{email}".encode("utf-8")).hexdigest()
        return self.cache_format % {"scope": self.scope, "ident": digest}


class TokenRefreshThrottle(AnonRateThrottle):
    scope = "auth_refresh"

    def get_cache_key(self, request, view):
        token = request.COOKIES.get(settings.AUTH_REFRESH_COOKIE, "")
        if not token:
            return super().get_cache_key(request, view)
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return self.cache_format % {"scope": self.scope, "ident": digest}


class LogoutThrottle(AnonRateThrottle):
    scope = "auth_logout"

    def get_cache_key(self, request, view):
        token = request.COOKIES.get(settings.AUTH_REFRESH_COOKIE, "")
        if not token:
            return super().get_cache_key(request, view)
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return self.cache_format % {"scope": self.scope, "ident": digest}


class SchoolTokenObtainPairView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [AnonRateThrottle, LoginThrottle]

    def post(self, request):
        SchoolJWTAuthentication.enforce_csrf(request)
        serializer = SchoolTokenObtainPairSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        response = Response({"detail": "Signed in."})
        _set_auth_cookies(response, serializer.validated_data["access"], serializer.validated_data["refresh"])
        return response


class SchoolTokenRefreshView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [TokenRefreshThrottle]

    def post(self, request):
        SchoolJWTAuthentication.enforce_csrf(request)
        refresh = request.COOKIES.get(settings.AUTH_REFRESH_COOKIE)
        if not refresh:
            return Response({"detail": "Your session has expired. Please sign in again."}, status=status.HTTP_401_UNAUTHORIZED)
        serializer = SchoolTokenRefreshSerializer(data={"refresh": refresh})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        response = Response({"detail": "Session refreshed."})
        _set_auth_cookies(response, data["access"], data.get("refresh"))
        return response


class SchoolTokenLogoutView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [LogoutThrottle]

    def post(self, request):
        SchoolJWTAuthentication.enforce_csrf(request)
        refresh = request.COOKIES.get(settings.AUTH_REFRESH_COOKIE)
        if refresh:
            try:
                RefreshToken(refresh).blacklist()
            except TokenError:
                pass
        response = Response(status=status.HTTP_205_RESET_CONTENT)
        _clear_auth_cookies(response)
        return response

urlpatterns = [
    path("health/live/", liveness_probe, name="health-live"),
    path("health/ready/", readiness_probe, name="health-ready"),
 path("api/system/status/", system_status, name="system-status"),
 path("api/system/logo/", system_logo, name="system-logo"),
 path("api/emergency-contacts/", PublicEmergencyContactListView.as_view(), name="public-emergency-contacts"),
 path("admin/", admin.site.urls), path("api/auth/csrf/", CsrfTokenView.as_view(), name="csrf-token"),
 path("api/auth/token/", SchoolTokenObtainPairView.as_view(), name="token_obtain_pair"),
 path("api/auth/token/refresh/", SchoolTokenRefreshView.as_view(), name="token_refresh"),
 path("api/auth/token/logout/", SchoolTokenLogoutView.as_view(), name="token_blacklist"),
 path("api/payments/webhook/", paystack_webhook, name="paystack-webhook"),
 path("api/accounts/", include("apps.accounts.urls")), path("api/products/", include("apps.products.urls")), path("api/jobs/", include("apps.jobs.urls")),
 path("api/payments/", include("apps.payments.urls")),
 path("api/ads/", include("apps.ads.urls")),
 path("api/lost-found/", include("apps.lost_found.urls")), path("api/messaging/", include("apps.messaging.urls")),
 path("api/notifications/", include("apps.notifications.urls")), path("api/admin-tools/", include("apps.administration.urls")),
 path("api/reviews/", include("apps.reviews.urls")),
 path("api/accommodation/", include("apps.accommodation.urls")),
]
if settings.DEBUG: urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

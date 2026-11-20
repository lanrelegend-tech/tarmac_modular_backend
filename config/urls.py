from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from apps.payments.webhooks import paystack_webhook
from apps.accounts.authentication import SchoolTokenObtainPairSerializer, SchoolTokenRefreshSerializer
from rest_framework_simplejwt.views import TokenBlacklistView
from apps.administration.views import system_status, system_logo

class SchoolTokenObtainPairView(TokenObtainPairView):
    serializer_class = SchoolTokenObtainPairSerializer

class SchoolTokenRefreshView(TokenRefreshView):
    serializer_class = SchoolTokenRefreshSerializer

urlpatterns = [
 path("api/system/status/", system_status, name="system-status"),
 path("api/system/logo/", system_logo, name="system-logo"),
 path("admin/", admin.site.urls), path("api/auth/token/", SchoolTokenObtainPairView.as_view(), name="token_obtain_pair"),
 path("api/auth/token/refresh/", SchoolTokenRefreshView.as_view(), name="token_refresh"),
 path("api/auth/token/logout/", TokenBlacklistView.as_view(), name="token_blacklist"),
 path("api/payments/webhook/", paystack_webhook, name="paystack-webhook"),
 path("api/accounts/", include("apps.accounts.urls")), path("api/products/", include("apps.products.urls")),
 path("api/orders/", include("apps.orders.urls")), path("api/payments/", include("apps.payments.urls")),
 path("api/ads/", include("apps.ads.urls")),
 path("api/lost-found/", include("apps.lost_found.urls")), path("api/messaging/", include("apps.messaging.urls")),
 path("api/notifications/", include("apps.notifications.urls")), path("api/admin-tools/", include("apps.administration.urls")),
 path("api/reviews/", include("apps.reviews.urls")),
]
if settings.DEBUG: urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

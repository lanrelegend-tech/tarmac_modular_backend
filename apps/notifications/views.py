from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework import status
from rest_framework.exceptions import ValidationError
from django.conf import settings
from django.core.cache import cache
from django.db.models import Q
from .models import PushSubscription
from rest_framework.permissions import BasePermission
from apps.accounts.permissions import is_student_feature_eligible
from .models import Notification
from .serializers import NotificationSerializer
from config.throttles import ActionRateLimitMixin


class CanReceiveTarmacNotifications(BasePermission):
    message = "An active Tarmac account is required."

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated and user.is_active and not user.is_suspended):
            return False
        return bool(user.is_staff or is_student_feature_eligible(user))


class NotificationViewSet(ActionRateLimitMixin, viewsets.ReadOnlyModelViewSet):
    action_throttle_scopes = {
        "push_subscription": "push_subscription_write",
        "mark_read": "push_subscription_write",
    }
    serializer_class=NotificationSerializer; permission_classes=[CanReceiveTarmacNotifications]
    http_method_names = ["get", "post", "delete", "head", "options"]
    def get_queryset(self):
        queryset = Notification.objects.filter(
            user=self.request.user,
            channel=Notification.Channel.IN_APP,
        )
        if self.request.user.is_staff and not self.request.user.is_superuser:
            assigned = self.request.user.staff_notification_sections or []
            queryset = queryset.filter(Q(data__staff_section__in=assigned) | Q(data__notification_category="account_security"))
        return queryset.order_by("-created_at", "-id")
    @action(detail=False, methods=["get", "post", "delete"], url_path="push-subscription")
    def push_subscription(self, request):
        if request.method == "GET":
            return Response({"enabled": bool(getattr(settings, "WEB_PUSH_PUBLIC_KEY", "")), "public_key": getattr(settings, "WEB_PUSH_PUBLIC_KEY", "")})
        if not getattr(settings, "WEB_PUSH_PUBLIC_KEY", "") or not getattr(settings, "WEB_PUSH_PRIVATE_KEY", ""):
            return Response({"detail": "Phone notifications are not configured yet."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        if request.method == "DELETE":
            endpoint = request.data.get("endpoint", "")
            PushSubscription.objects.filter(user=request.user, endpoint=endpoint).delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        subscription = request.data.get("subscription") or {}
        if not isinstance(subscription, dict):
            raise ValidationError({"subscription": "Provide a valid browser push subscription."})
        keys = subscription.get("keys") or {}
        if not isinstance(keys, dict):
            raise ValidationError({"subscription": "Provide a valid browser push subscription."})
        endpoint = subscription.get("endpoint", "")
        from config.push_validation import validate_push_subscription
        if not validate_push_subscription(endpoint, keys.get("p256dh"), keys.get("auth")):
            raise ValidationError({"subscription": "Provide a valid browser push subscription."})
        PushSubscription.objects.update_or_create(endpoint=endpoint, defaults={"user": request.user, "p256dh": keys["p256dh"], "auth": keys["auth"]})
        return Response({"enabled": True}, status=status.HTTP_201_CREATED)
    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        cache_key = f"tarmac.notifications.unread-count.{request.user.pk}"
        count = cache.get(cache_key)
        if count is None:
            count = self.get_queryset().filter(is_read=False).count()
            cache.set(cache_key, count, 3)
        return Response({"count": count})
    def destroy(self, request, *args, **kwargs):
        notification = self.get_object()
        notification.delete()
        return Response(status=204)
    @action(detail=True, methods=["post"], url_path="mark-read")
    def mark_read(self, request, pk=None):
        notification = self.get_object()
        notification.is_read = True
        notification.save(update_fields=("is_read",))
        return Response({"id": notification.pk, "is_read": True})

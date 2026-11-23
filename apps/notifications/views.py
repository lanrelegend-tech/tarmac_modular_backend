from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework import status
from rest_framework.exceptions import ValidationError
from django.conf import settings
from django.core.cache import cache
from .models import PushSubscription
from apps.accounts.permissions import IsVerifiedStudent
from .models import Notification
from .serializers import NotificationSerializer
class NotificationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class=NotificationSerializer; permission_classes=[IsVerifiedStudent]
    http_method_names = ["get", "post", "delete", "head", "options"]
    def get_queryset(self):
        return Notification.objects.filter(
            user=self.request.user,
            channel=Notification.Channel.IN_APP,
        ).order_by("-created_at", "-id")
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

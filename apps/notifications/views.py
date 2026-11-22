from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
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
    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        return Response({"count": self.get_queryset().filter(is_read=False).count()})
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

from rest_framework import viewsets,permissions
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import PermissionDenied, ValidationError
from apps.accounts.permissions import IsVerifiedStudent
from apps.administration.permissions import PlatformFeatureEnabled
from django.utils import timezone
from django.http import FileResponse
from django.shortcuts import get_object_or_404
import mimetypes
from .models import Conversation,Message,MessageAttachment
from .serializers import ConversationSerializer,MessageSerializer
class ConversationViewSet(viewsets.ModelViewSet):
    platform_feature = "messaging_enabled"
    serializer_class=ConversationSerializer; permission_classes=[IsVerifiedStudent, PlatformFeatureEnabled]
    def get_queryset(self): return Conversation.objects.filter(participants=self.request.user)
    def perform_create(self,serializer):
        participants = serializer.validated_data.get("participants", [])
        other_users = [user for user in participants if user.pk != self.request.user.pk]
        if len(other_users) != 1 or not other_users[0].is_active or other_users[0].is_suspended or not other_users[0].email_verified or not other_users[0].is_student_verified:
            raise ValidationError({"participants": "A conversation must be with exactly one other active, verified student."})
        convo=serializer.save(); convo.participants.add(self.request.user)
    @action(detail=True, methods=["post"], url_path="mark-read")
    def mark_read(self, request, pk=None):
        conversation = self.get_object()
        conversation.messages.filter(read_at__isnull=True).exclude(sender=request.user).update(read_at=timezone.now())
        return Response({"conversation": conversation.pk, "read": True})
class MessageViewSet(viewsets.ModelViewSet):
    platform_feature = "messaging_enabled"
    serializer_class=MessageSerializer; permission_classes=[IsVerifiedStudent, PlatformFeatureEnabled]
    def get_queryset(self):
        queryset = Message.objects.filter(conversation__participants=self.request.user)
        conversation_id = self.request.query_params.get("conversation")
        return queryset.filter(conversation_id=conversation_id) if conversation_id else queryset
    def perform_create(self,serializer):
        convo=serializer.validated_data["conversation"]
        if not convo.participants.filter(pk=self.request.user.pk).exists(): raise PermissionDenied("You are not a participant in this conversation.")
        serializer.save(sender=self.request.user)
    def perform_update(self, serializer):
        if serializer.instance.sender_id != self.request.user.id:
            raise PermissionDenied("Only the message sender can edit this message.")
        serializer.save()
    def perform_destroy(self, instance):
        if instance.sender_id != self.request.user.id:
            raise PermissionDenied("Only the message sender can delete this message.")
        instance.delete()

@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def message_attachment_file(request, attachment_id):
    attachment = get_object_or_404(MessageAttachment.objects.select_related("message__conversation"), pk=attachment_id)
    if not attachment.message.conversation.participants.filter(pk=request.user.pk).exists():
        raise PermissionDenied("You are not a participant in this conversation.")
    content_type = attachment.content_type or mimetypes.guess_type(attachment.file.name)[0] or "application/octet-stream"
    return FileResponse(attachment.file.open("rb"), content_type=content_type)

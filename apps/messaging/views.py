from rest_framework import viewsets,permissions
from rest_framework.exceptions import PermissionDenied, ValidationError
from apps.accounts.permissions import IsVerifiedStudent
from apps.administration.permissions import PlatformFeatureEnabled
from .models import Conversation,Message
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

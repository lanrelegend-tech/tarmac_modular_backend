from rest_framework import serializers
from .models import Conversation,Message
class ConversationSerializer(serializers.ModelSerializer):
    class Meta: model=Conversation; fields=("id","participants","created_at"); read_only_fields=("created_at",)

    def validate(self, attrs):
        if self.instance and "participants" in attrs:
            current = set(self.instance.participants.values_list("pk", flat=True))
            requested = {user.pk for user in attrs["participants"]}
            if requested != current:
                raise serializers.ValidationError({"participants": "Conversation membership cannot be changed."})
        return attrs

class MessageSerializer(serializers.ModelSerializer):
    class Meta: model=Message; fields=("id","conversation","sender","body","created_at","read_at"); read_only_fields=("sender","created_at","read_at")

    def validate_conversation(self, conversation):
        if self.instance and conversation.pk != self.instance.conversation_id:
            raise serializers.ValidationError("A message cannot be moved to another conversation.")
        return conversation

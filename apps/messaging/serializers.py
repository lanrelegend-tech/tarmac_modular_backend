from rest_framework import serializers
from django.utils.text import get_valid_filename
from django.db import transaction
from .models import Conversation,Message,MessageAttachment

class MessageAttachmentSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()
    class Meta:
        model = MessageAttachment
        fields = ("id", "original_name", "content_type", "file_url", "created_at")
        read_only_fields = fields
    def get_file_url(self, obj):
        request = self.context.get("request")
        path = f"/api/messaging/attachments/{obj.pk}/file/"
        return request.build_absolute_uri(path) if request else path

class ConversationSerializer(serializers.ModelSerializer):
    class Meta: model=Conversation; fields=("id","participants","status","created_at","updated_at"); read_only_fields=("status","created_at","updated_at")

    def validate(self, attrs):
        if self.instance and "participants" in attrs:
            current = set(self.instance.participants.values_list("pk", flat=True))
            requested = {user.pk for user in attrs["participants"]}
            if requested != current:
                raise serializers.ValidationError({"participants": "Conversation membership cannot be changed."})
        return attrs

class MessageSerializer(serializers.ModelSerializer):
    attachments = MessageAttachmentSerializer(many=True, read_only=True)
    attachment_files = serializers.ListField(child=serializers.FileField(), write_only=True, required=False, max_length=5)
    body = serializers.CharField(required=False, allow_blank=True, max_length=5000)
    class Meta: model=Message; fields=("id","conversation","sender","body","attachments","attachment_files","created_at","read_at"); read_only_fields=("sender","created_at","read_at")

    def validate_conversation(self, conversation):
        if self.instance and conversation.pk != self.instance.conversation_id:
            raise serializers.ValidationError("A message cannot be moved to another conversation.")
        return conversation

    def validate(self, attrs):
        uploads = attrs.get("attachment_files", [])
        if not str(attrs.get("body", "")).strip() and not uploads:
            raise serializers.ValidationError({"body": "Write a message or attach a file."})
        for upload in uploads:
            if upload.size > 5 * 1024 * 1024 or upload.content_type not in ("image/jpeg", "image/png", "image/webp", "application/pdf"):
                raise serializers.ValidationError({"attachment_files": "Use JPG, PNG, WEBP or PDF files no larger than 5 MB."})
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        uploads = validated_data.pop("attachment_files", [])
        message = Message.objects.create(**validated_data)
        for upload in uploads:
            MessageAttachment.objects.create(message=message, file=upload, original_name=get_valid_filename(upload.name)[:255], content_type=upload.content_type)
        return message

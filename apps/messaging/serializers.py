from rest_framework import serializers
from django.utils.text import get_valid_filename
from django.db import transaction
from .models import Conversation,Message,MessageAttachment

class MessageAttachmentSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()
    is_image = serializers.SerializerMethodField()
    class Meta:
        model = MessageAttachment
        fields = ("id", "original_name", "content_type", "file_url", "is_image", "created_at")
        read_only_fields = fields
    def get_file_url(self, obj):
        request = self.context.get("request")
        path = f"/api/messaging/attachments/{obj.pk}/file/"
        return request.build_absolute_uri(path) if request else path
    def get_is_image(self, obj):
        return (obj.content_type or "").startswith("image/")

class ConversationSerializer(serializers.ModelSerializer):
    participants_data = serializers.SerializerMethodField()
    completed_by = serializers.PrimaryKeyRelatedField(many=True, read_only=True)
    class Meta: model=Conversation; fields=("id","participants","participants_data","status","context_type","context_id","context_title","is_official","completed_by","completed_at","created_at","updated_at"); read_only_fields=("status","is_official","completed_by","completed_at","created_at","updated_at")

    def get_participants_data(self, obj):
        return [{"id": user.pk, "name": user.get_full_name().strip() or user.username, "is_staff": user.is_staff} for user in obj.participants.all()]

    def validate(self, attrs):
        if self.instance and "participants" in attrs:
            current = set(self.instance.participants.values_list("pk", flat=True))
            requested = {user.pk for user in attrs["participants"]}
            if requested != current:
                raise serializers.ValidationError({"participants": "Conversation membership cannot be changed."})
        if self.instance:
            for field in ("context_type", "context_id", "context_title"):
                if field in attrs and attrs[field] != getattr(self.instance, field):
                    raise serializers.ValidationError({field: "Conversation context cannot be changed."})
            return attrs
        context_type = attrs.get("context_type", Conversation.ContextType.GENERAL)
        if context_type != Conversation.ContextType.GENERAL and context_type != Conversation.ContextType.MARKETPLACE:
            raise serializers.ValidationError({"context_type": "This conversation type can only be created by the corresponding Tarmac feature."})
        if context_type == Conversation.ContextType.MARKETPLACE:
            from apps.products.models import Product
            product_id = attrs.get("context_id")
            participants = attrs.get("participants", [])
            if not product_id or len([user for user in participants]) != 2:
                raise serializers.ValidationError({"context_id": "Choose a valid marketplace listing and its seller."})
            product = Product.objects.filter(pk=product_id, status=Product.Status.ACTIVE).first()
            if not product or product.seller_id not in {user.pk for user in participants}:
                raise serializers.ValidationError({"context_id": "The seller must be part of the conversation for this active listing."})
            attrs["context_title"] = product.title
        return attrs

class MessageSerializer(serializers.ModelSerializer):
    attachments = MessageAttachmentSerializer(many=True, read_only=True)
    attachment_files = serializers.ListField(child=serializers.FileField(), write_only=True, required=False, max_length=5)
    body = serializers.CharField(required=False, allow_blank=True, max_length=5000)
    sender_name = serializers.SerializerMethodField()
    sender_is_official = serializers.BooleanField(source="sender.is_staff", read_only=True)
    class Meta: model=Message; fields=("id","conversation","sender","sender_name","sender_is_official","body","attachments","attachment_files","created_at","read_at"); read_only_fields=("sender","created_at","read_at")

    def get_sender_name(self, obj):
        return "Tarmac Administration" if obj.sender.is_staff else (obj.sender.get_full_name().strip() or obj.sender.username)

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

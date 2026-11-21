from rest_framework import serializers
from django.utils.text import get_valid_filename
from django.db import transaction
from .models import Conversation,Message,MessageAttachment,ConversationParticipantSnapshot,BlockedUser
from apps.accounts.models import User

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
    participants = serializers.PrimaryKeyRelatedField(many=True, queryset=User.objects.all(), write_only=True, required=False)
    participants_data = serializers.SerializerMethodField()
    completed_by_me = serializers.SerializerMethodField()
    completion_count = serializers.SerializerMethodField()
    class Meta: model=Conversation; fields=("id","participants","participants_data","status","context_type","context_id","context_title","is_official","completed_by_me","completion_count","completed_at","created_at","updated_at"); read_only_fields=("status","is_official","completed_by_me","completion_count","completed_at","created_at","updated_at")

    def get_participants_data(self, obj):
        request = self.context.get("request")
        snapshots = {row.user_id: row for row in obj.participant_snapshots.all()}
        data = []
        for user in obj.participants.all():
            snapshot = snapshots.get(user.pk)
            if not snapshot:
                name = "Tarmac Administration" if user.is_staff else (user.get_full_name().strip() or user.username)
                snapshot = create_participant_snapshot(obj, user, name=name)
            data.append({"is_me": bool(request and user.pk == request.user.pk), "name": "Tarmac Support" if user.is_staff else snapshot.display_name, "is_staff": user.is_staff, "identity_type": snapshot.identity_type, "public_identity_id": snapshot.public_identity_id, "student_status": snapshot.verification_badge, "last_seen_at": user.last_seen_at})
        return data

    def get_completed_by_me(self, obj):
        request = self.context.get("request")
        return bool(request and obj.completed_by.filter(pk=request.user.pk).exists())

    def get_completion_count(self, obj):
        return obj.completed_by.count()

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
        if context_type not in (Conversation.ContextType.GENERAL, Conversation.ContextType.MARKETPLACE):
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
            raise serializers.ValidationError({"context_type": "Use the marketplace contact endpoint so the listing audience and public identity are checked."})
            attrs["context_title"] = product.title
        return attrs

class MessageSerializer(serializers.ModelSerializer):
    attachments = MessageAttachmentSerializer(many=True, read_only=True)
    attachment_files = serializers.ListField(child=serializers.FileField(), write_only=True, required=False, max_length=5)
    body = serializers.CharField(required=False, allow_blank=True, max_length=5000)
    sender_name = serializers.SerializerMethodField()
    sender_is_official = serializers.BooleanField(source="sender.is_staff", read_only=True)
    is_mine = serializers.SerializerMethodField()
    class Meta: model=Message; fields=("id","conversation","sender_name","sender_is_official","is_mine","body","attachments","attachment_files","created_at","read_at"); read_only_fields=("sender_name","sender_is_official","is_mine","created_at","read_at")

    def get_sender_name(self, obj):
        if obj.sender.is_staff:
            return "Tarmac Administration"
        snapshot = obj.conversation.participant_snapshots.filter(user=obj.sender).first()
        return snapshot.display_name if snapshot else "Student"

    def get_is_mine(self, obj):
        request = self.context.get("request")
        return bool(request and obj.sender_id == request.user.pk)

    def validate_conversation(self, conversation):
        if self.instance and conversation.pk != self.instance.conversation_id:
            raise serializers.ValidationError("A message cannot be moved to another conversation.")
        return conversation


def create_participant_snapshot(conversation, user, identity_type="normal", name=None):
    from apps.accounts.models import AnonymousMarketplaceIdentity
    identity = None
    if identity_type == "anonymous":
        identity = AnonymousMarketplaceIdentity.objects.filter(user=user, is_enabled=True).first()
        if not identity:
            identity_type = "normal"
    if identity:
        display_name = identity.handle
        public_id = str(identity.public_id)
    else:
        normal_name = user.get_full_name().strip() if user.public_name_enabled else ""
        display_name = name or ("Tarmac Administration" if user.is_staff else (normal_name or user.username))
        public_id = str(user.public_id)
    return ConversationParticipantSnapshot.objects.update_or_create(
        conversation=conversation, user=user,
        defaults={"identity_type": identity_type, "display_name": display_name, "public_identity_id": public_id, "verification_badge": user.student_status if user.is_student_verified else "pending"},
    )[0]


class BlockedUserSerializer(serializers.ModelSerializer):
    user_public_id = serializers.UUIDField(write_only=True)
    display_name = serializers.SerializerMethodField()
    public_id = serializers.SerializerMethodField()
    class Meta:
        model = BlockedUser
        fields = ("id", "user_public_id", "public_id", "display_name", "created_at")
        read_only_fields = ("id", "public_id", "display_name", "created_at")
    def validate_user_public_id(self, value):
        from apps.accounts.models import User
        from apps.accounts.permissions import is_student_feature_eligible
        target = User.objects.filter(public_id=value).first()
        if not target or target.pk == self.context["request"].user.pk or not is_student_feature_eligible(target):
            raise serializers.ValidationError("Choose another active, verified student account.")
        if BlockedUser.objects.filter(blocked_by=self.context["request"].user, blocked_user=target).exists():
            raise serializers.ValidationError("You already blocked this account.")
        return value
    def create(self, validated_data):
        from apps.accounts.models import User
        public_id = validated_data.pop("user_public_id")
        target = User.objects.get(public_id=public_id)
        return BlockedUser.objects.create(blocked_by=self.context["request"].user, blocked_user=target)
    def get_public_id(self, obj):
        return str(obj.blocked_user.public_id)
    def get_display_name(self, obj):
        user = obj.blocked_user
        return (user.get_full_name().strip() if user.public_name_enabled else "") or user.username

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

from rest_framework import viewsets,permissions
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import PermissionDenied, ValidationError
from apps.accounts.permissions import IsVerifiedStudent, is_student_feature_eligible
from apps.administration.permissions import PlatformFeatureEnabled
from django.utils import timezone
from django.db import transaction
from django.http import FileResponse
from django.shortcuts import get_object_or_404
import mimetypes
from .models import Conversation,Message,MessageAttachment
from .serializers import ConversationSerializer,MessageSerializer
from apps.notifications.services import notify_user
class ConversationViewSet(viewsets.ModelViewSet):
    platform_feature = "messaging_enabled"
    serializer_class=ConversationSerializer; permission_classes=[IsVerifiedStudent, PlatformFeatureEnabled]
    def get_queryset(self): return Conversation.objects.filter(participants=self.request.user)
    def perform_create(self,serializer):
        participants = serializer.validated_data.get("participants", [])
        other_users = [user for user in participants if user.pk != self.request.user.pk]
        if len(other_users) != 1 or not is_student_feature_eligible(other_users[0]):
            raise ValidationError({"participants": "A conversation must be with exactly one other active student account."})
        convo=serializer.save(); convo.participants.add(self.request.user)
    @action(detail=True, methods=["post"], url_path="mark-read")
    def mark_read(self, request, pk=None):
        conversation = self.get_object()
        conversation.messages.filter(read_at__isnull=True).exclude(sender=request.user).update(read_at=timezone.now())
        return Response({"conversation": conversation.pk, "read": True})
    @action(detail=True, methods=["post"], url_path="link-marketplace")
    def link_marketplace(self, request, pk=None):
        conversation = self.get_object()
        try:
            product_id = int(request.data.get("product_id"))
        except (TypeError, ValueError):
            raise ValidationError({"product_id": "Choose a valid marketplace listing."})
        from apps.products.models import Product
        product = Product.objects.filter(pk=product_id, status=Product.Status.ACTIVE).first()
        participants = list(conversation.participants.all())
        if not product or len(participants) != 2 or product.seller_id not in {person.pk for person in participants}:
            raise ValidationError({"product_id": "The active listing's seller must be part of this conversation."})
        if conversation.context_type == Conversation.ContextType.GENERAL:
            conversation.context_type = Conversation.ContextType.MARKETPLACE
            conversation.context_id = product.pk
            conversation.context_title = product.title
            conversation.save(update_fields=("context_type", "context_id", "context_title", "updated_at"))
        elif conversation.context_type != Conversation.ContextType.MARKETPLACE or conversation.context_id != product.pk:
            raise ValidationError({"detail": "This conversation is linked to a different Tarmac feature."})
        return Response(self.get_serializer(conversation).data)
    @action(detail=True, methods=["post"])
    @transaction.atomic
    def complete(self, request, pk=None):
        conversation = Conversation.objects.select_for_update().get(pk=self.get_object().pk)
        if conversation.context_type not in (Conversation.ContextType.GENERAL, Conversation.ContextType.MARKETPLACE, Conversation.ContextType.LOST_FOUND):
            raise ValidationError({"detail": "Only student conversations can be completed here."})
        participants = list(conversation.participants.filter(is_staff=False))
        if request.user not in participants or len(participants) != 2:
            raise PermissionDenied("Only the two students in this conversation can confirm completion.")
        if conversation.status == Conversation.Status.RESOLVED:
            return Response(self.get_serializer(conversation).data)
        if request.user in conversation.completed_by.all():
            return Response(self.get_serializer(conversation).data)

        conversation.completed_by.add(request.user)
        completion_event_at = timezone.now()
        confirmed_ids = set(conversation.completed_by.values_list("pk", flat=True))
        all_confirmed = all(person.pk in confirmed_ids for person in participants)
        if all_confirmed:
            conversation.status = Conversation.Status.RESOLVED
            conversation.completed_at = completion_event_at
            if conversation.context_type == Conversation.ContextType.MARKETPLACE:
                from apps.products.models import Product
                product = Product.objects.select_for_update().filter(pk=conversation.context_id, seller_id__in=[person.pk for person in participants]).first()
                if product and product.status == Product.Status.ACTIVE:
                    product.status = Product.Status.SOLD
                    product.save(update_fields=("status", "updated_at"))
            elif conversation.context_type == Conversation.ContextType.LOST_FOUND:
                from apps.lost_found.models import ItemMatch, LostFoundReport
                match = ItemMatch.objects.select_for_update().filter(pk=conversation.context_id, is_dismissed=False).select_related("lost_report", "found_report").first()
                if match and {match.lost_report.reporter_id, match.found_report.reporter_id} == {person.pk for person in participants}:
                    LostFoundReport.objects.filter(pk__in=(match.lost_report_id, match.found_report_id)).update(status=LostFoundReport.Status.RETURNED, is_active=False)
            subject = "Tarmac: completion confirmed"
            context_name = {Conversation.ContextType.MARKETPLACE: "marketplace transaction", Conversation.ContextType.LOST_FOUND: "lost-and-found return"}.get(conversation.context_type, "student conversation")
            body = f"Both students confirmed this {context_name} as complete."
            for person in participants:
                notify_user(person, subject, body, {"kind": "conversation_completed", "conversation_id": conversation.pk, "context_type": conversation.context_type}, f"conversation-completed:{conversation.pk}:{completion_event_at.timestamp()}:{person.pk}")
        else:
            other = next(person for person in participants if person.pk != request.user.pk)
            notify_user(other, "Tarmac: please confirm completion", f"{request.user.get_full_name().strip() or request.user.username} marked this {conversation.context_type.replace('_', ' ')} as complete. Confirm it in your Tarmac messages if everything is finished.", {"kind": "conversation_completion_requested", "conversation_id": conversation.pk}, f"conversation-completion-request:{conversation.pk}:{completion_event_at.timestamp()}:{request.user.pk}:{other.pk}")
        conversation.updated_at = completion_event_at
        conversation.save(update_fields=("status", "completed_at", "updated_at"))
        return Response(self.get_serializer(conversation).data)
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
        if convo.status == Conversation.Status.RESOLVED:
            convo.status = Conversation.Status.OPEN
            convo.completed_at = None
            convo.completed_by.clear()
        convo.updated_at = timezone.now()
        convo.save(update_fields=("status", "completed_at", "updated_at"))
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
    if not request.user.is_staff and not attachment.message.conversation.participants.filter(pk=request.user.pk).exists():
        raise PermissionDenied("You are not a participant in this conversation.")
    content_type = attachment.content_type or mimetypes.guess_type(attachment.file.name)[0] or "application/octet-stream"
    return FileResponse(attachment.file.open("rb"), content_type=content_type)

from rest_framework import viewsets,permissions
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import PermissionDenied, ValidationError
from apps.accounts.permissions import IsVerifiedStudent, is_student_feature_eligible
from apps.administration.permissions import PlatformFeatureEnabled
from django.utils import timezone
from django.db import transaction
from django.db.models import Count, Exists, IntegerField, OuterRef, Subquery, Value
from django.db.models.functions import Coalesce
from django.http import FileResponse
from django.shortcuts import get_object_or_404
import mimetypes
from .models import Conversation,Message,MessageAttachment
from .serializers import ConversationSerializer,MessageSerializer,BlockedUserSerializer
from .models import BlockedUser
from apps.notifications.services import notify_user
class ConversationViewSet(viewsets.ModelViewSet):
    platform_feature = "messaging_enabled"
    serializer_class=ConversationSerializer; permission_classes=[IsVerifiedStudent, PlatformFeatureEnabled]
    def get_queryset(self):
        latest_message = Message.objects.filter(conversation_id=OuterRef("pk")).order_by("-created_at", "-pk")
        unread_messages = Message.objects.filter(conversation_id=OuterRef("pk"), read_at__isnull=True).exclude(sender=self.request.user).values("conversation_id").annotate(total=Count("pk")).values("total")[:1]
        completed_by_me = Conversation.completed_by.through.objects.filter(conversation_id=OuterRef("pk"), user_id=self.request.user.pk)
        queryset = Conversation.objects.filter(participants=self.request.user).prefetch_related("participants", "participant_snapshots").annotate(
            inbox_last_message_preview=Subquery(latest_message.values("body")[:1]),
            inbox_last_message_at=Subquery(latest_message.values("created_at")[:1]),
            inbox_unread_count=Coalesce(Subquery(unread_messages, output_field=IntegerField()), Value(0), output_field=IntegerField()),
            inbox_completion_count=Count("completed_by", distinct=True),
            inbox_completed_by_me=Exists(completed_by_me),
        ).order_by("-updated_at", "-pk")
        identity_type = self.request.query_params.get("identity_type")
        if identity_type in ("normal", "anonymous"):
            queryset = queryset.filter(participant_snapshots__user=self.request.user, participant_snapshots__identity_type=identity_type)
        return queryset.distinct()
    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        count = Message.objects.filter(
            conversation__participants=request.user,
            read_at__isnull=True,
        ).exclude(sender=request.user).count()
        return Response({"count": count})
    def create(self, request, *args, **kwargs):
        raise PermissionDenied("Start a conversation from a marketplace listing, lost-and-found match, or official support message.")
    @action(detail=True, methods=["post"], url_path="block")
    def block(self, request, pk=None):
        conversation = self.get_object()
        if conversation.is_official:
            raise PermissionDenied("Official Tarmac messages cannot be blocked here. Contact support if you need help.")
        other = conversation.participants.exclude(pk=request.user.pk).filter(is_staff=False).first()
        if not other:
            raise ValidationError("This conversation does not have another student to block.")
        BlockedUser.objects.get_or_create(blocked_by=request.user, blocked_user=other)
        return Response({"blocked": True})
    def perform_create(self,serializer):
        participants = serializer.validated_data.get("participants", [])
        other_users = [user for user in participants if user.pk != self.request.user.pk]
        if len(other_users) != 1 or not is_student_feature_eligible(other_users[0]):
            raise ValidationError({"participants": "A conversation must be with exactly one other active student account."})
        convo=serializer.save(); convo.participants.add(self.request.user)
        from .serializers import create_participant_snapshot
        for person in convo.participants.all():
            create_participant_snapshot(convo, person)

    @action(detail=False, methods=["post"], url_path="start-marketplace")
    @transaction.atomic
    def start_marketplace(self, request):
        from apps.products.models import Product, ProductAudienceGrant
        from .serializers import create_participant_snapshot
        try:
            product_id = int(request.data.get("product_id"))
        except (TypeError, ValueError):
            raise ValidationError({"product_id": "Choose a valid listing."})
        product = Product.objects.select_for_update().select_related("seller", "anonymous_identity").filter(pk=product_id, status=Product.Status.ACTIVE, seller__is_active=True, seller__is_suspended=False).first()
        if not product or product.seller_id == request.user.pk:
            raise ValidationError({"product_id": "This listing cannot be contacted."})
        if product.identity_type == Product.IdentityType.ANONYMOUS and (not product.anonymous_identity_id or not product.anonymous_identity.is_enabled):
            raise ValidationError({"product_id": "This marketplace identity is unavailable."})
        if product.visibility == Product.Visibility.DRAFT:
            raise PermissionDenied("This listing is private.")
        if product.visibility == Product.Visibility.INVITE_ONLY and not ProductAudienceGrant.objects.filter(product=product, user=request.user).exists():
            raise PermissionDenied("This listing is only visible to students invited by its owner.")
        if not is_student_feature_eligible(product.seller):
            raise ValidationError({"product_id": "This seller cannot currently receive messages."})
        if BlockedUser.objects.filter(blocked_by__in=(request.user, product.seller), blocked_user__in=(request.user, product.seller)).exists():
            raise PermissionDenied("Messaging is unavailable because one account has blocked the other.")
        identity_type = request.data.get("identity_type", "normal")
        if identity_type not in ("normal", "anonymous"):
            raise ValidationError({"identity_type": "Choose normal or anonymous identity."})
        if identity_type == "anonymous" and (not getattr(request.user, "anonymous_marketplace_identity", None) or not request.user.anonymous_marketplace_identity.is_enabled):
            raise ValidationError({"identity_type": "Create an anonymous marketplace identity first."})
        existing_conversations = Conversation.objects.filter(context_type=Conversation.ContextType.MARKETPLACE, context_id=product.pk, participants=request.user).filter(participants=product.seller).prefetch_related("participant_snapshots")
        expected_identity = getattr(request.user, "anonymous_marketplace_identity", None) if identity_type == "anonymous" else None
        target_identity_type = "anonymous" if product.identity_type == Product.IdentityType.ANONYMOUS else "normal"
        target_public_id = str(product.anonymous_identity.public_id) if target_identity_type == "anonymous" else str(product.seller.public_id)
        conversation = next((row for row in existing_conversations if (snapshot := next((item for item in row.participant_snapshots.all() if item.user_id == request.user.pk), None)) and snapshot.identity_type == identity_type and snapshot.public_identity_id == (str(expected_identity.public_id) if expected_identity else str(request.user.public_id)) and (target_snapshot := next((item for item in row.participant_snapshots.all() if item.user_id == product.seller_id), None)) and target_snapshot.identity_type == target_identity_type and target_snapshot.public_identity_id == target_public_id), None)
        if not conversation:
            conversation = Conversation.objects.create(context_type=Conversation.ContextType.MARKETPLACE, context_id=product.pk, context_title=product.title)
            conversation.participants.add(request.user, product.seller)
            create_participant_snapshot(conversation, request.user, identity_type=identity_type)
            create_participant_snapshot(conversation, product.seller, identity_type="anonymous" if product.identity_type == Product.IdentityType.ANONYMOUS else "normal")
        return Response(self.get_serializer(conversation).data, status=201)

    @action(detail=False, methods=["post"], url_path="start-job")
    @transaction.atomic
    def start_job(self, request):
        from apps.jobs.models import JobListing
        from .serializers import create_participant_snapshot
        try:
            job_id = int(request.data.get("job_id"))
        except (TypeError, ValueError):
            raise ValidationError({"job_id": "Choose a valid job or gig."})
        job = JobListing.objects.select_for_update().select_related("owner", "anonymous_identity").filter(
            pk=job_id, status=JobListing.Status.ACTIVE, deadline__gte=timezone.localdate()
        ).first()
        if not job or not job.owner_id or job.owner_id == request.user.pk:
            raise ValidationError({"job_id": "This student listing cannot be contacted."})
        if not is_student_feature_eligible(job.owner):
            raise ValidationError({"job_id": "This student cannot currently receive messages."})
        if BlockedUser.objects.filter(blocked_by__in=(request.user, job.owner), blocked_user__in=(request.user, job.owner)).exists():
            raise PermissionDenied("Messaging is unavailable because one account has blocked the other.")
        identity_type = request.data.get("identity_type", "normal")
        if identity_type not in ("normal", "anonymous"):
            raise ValidationError({"identity_type": "Choose normal or anonymous identity."})
        my_alias = getattr(request.user, "anonymous_marketplace_identity", None) if identity_type == "anonymous" else None
        if identity_type == "anonymous" and (not my_alias or not my_alias.is_enabled):
            raise ValidationError({"identity_type": "Create and enable an anonymous marketplace identity first."})
        target_identity_type = job.identity_type if job.identity_type == "anonymous" and job.anonymous_identity_id and job.anonymous_identity.is_enabled else "normal"
        candidates = Conversation.objects.filter(
            context_type=Conversation.ContextType.JOBS, context_id=job.pk,
            participants=request.user,
        ).filter(participants=job.owner).prefetch_related("participant_snapshots")
        my_public_id = str(my_alias.public_id) if my_alias else str(request.user.public_id)
        target_public_id = str(job.anonymous_identity.public_id) if target_identity_type == "anonymous" else str(job.owner.public_id)
        conversation = next((row for row in candidates if (mine := next((snap for snap in row.participant_snapshots.all() if snap.user_id == request.user.pk), None)) and mine.identity_type == identity_type and mine.public_identity_id == my_public_id and (theirs := next((snap for snap in row.participant_snapshots.all() if snap.user_id == job.owner_id), None)) and theirs.identity_type == target_identity_type and theirs.public_identity_id == target_public_id), None)
        if not conversation:
            conversation = Conversation.objects.create(
                context_type=Conversation.ContextType.JOBS,
                context_id=job.pk, context_title=job.title,
            )
            conversation.participants.add(request.user, job.owner)
            create_participant_snapshot(conversation, request.user, identity_type=identity_type)
            create_participant_snapshot(conversation, job.owner, identity_type=target_identity_type)
        return Response(self.get_serializer(conversation).data, status=201)

    @action(detail=False, methods=["post"], url_path="start-profile")
    @transaction.atomic
    def start_profile(self, request):
        from apps.accounts.models import AnonymousMarketplaceIdentity, User
        from .serializers import create_participant_snapshot
        public_id = request.data.get("identity_public_id")
        identity_type = request.data.get("identity_type", "normal")
        my_identity_type = request.data.get("my_identity_type", "normal")
        if identity_type not in ("normal", "anonymous") or not public_id:
            raise ValidationError({"identity_public_id": "Choose a valid public marketplace profile."})
        if my_identity_type not in ("normal", "anonymous"):
            raise ValidationError({"my_identity_type": "Choose normal or anonymous identity."})
        alias = None
        if identity_type == "anonymous":
            alias = AnonymousMarketplaceIdentity.objects.select_related("user").filter(public_id=public_id, is_enabled=True).first()
            target = alias.user if alias else None
        else:
            target = User.objects.filter(public_id=public_id, public_profile_enabled=True).first()
        if not target or target.pk == request.user.pk or not is_student_feature_eligible(target):
            raise ValidationError({"identity_public_id": "This marketplace profile is not available for messaging."})
        if BlockedUser.objects.filter(blocked_by__in=(request.user, target), blocked_user__in=(request.user, target)).exists():
            raise PermissionDenied("Messaging is unavailable because one account has blocked the other.")
        expected_public_id = str(alias.public_id) if alias else str(target.public_id)
        my_alias = getattr(request.user, "anonymous_marketplace_identity", None) if my_identity_type == "anonymous" else None
        if my_identity_type == "anonymous" and (not my_alias or not my_alias.is_enabled):
            raise ValidationError({"my_identity_type": "Create and enable an anonymous marketplace identity first."})
        candidates = Conversation.objects.filter(context_type=Conversation.ContextType.GENERAL, participants=request.user).filter(participants=target).prefetch_related("participant_snapshots")
        expected_my_public_id = str(my_alias.public_id) if my_alias else str(request.user.public_id)
        conversation = next((row for row in candidates if (snapshot := next((item for item in row.participant_snapshots.all() if item.user_id == target.pk), None)) and snapshot.identity_type == identity_type and snapshot.public_identity_id == expected_public_id and (my_snapshot := next((item for item in row.participant_snapshots.all() if item.user_id == request.user.pk), None)) and my_snapshot.identity_type == my_identity_type and my_snapshot.public_identity_id == expected_my_public_id), None)
        if not conversation:
            conversation = Conversation.objects.create(context_type=Conversation.ContextType.GENERAL, context_title="Marketplace profile")
            conversation.participants.add(request.user, target)
            create_participant_snapshot(conversation, request.user, identity_type=my_identity_type)
            create_participant_snapshot(conversation, target, identity_type=identity_type)
        return Response(self.get_serializer(conversation).data, status=201)
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
        product = Product.objects.select_related("seller", "anonymous_identity").filter(pk=product_id, status=Product.Status.ACTIVE, seller__is_active=True, seller__is_suspended=False).first()
        participants = list(conversation.participants.all())
        if not product or len(participants) != 2 or product.seller_id not in {person.pk for person in participants}:
            raise ValidationError({"product_id": "The active listing's seller must be part of this conversation."})
        if product.visibility == Product.Visibility.INVITE_ONLY and not product.audience_grants.filter(user=request.user).exists():
            raise PermissionDenied("This listing is only visible to students invited by its owner.")
        if product.identity_type == Product.IdentityType.ANONYMOUS and (not product.anonymous_identity_id or not product.anonymous_identity.is_enabled):
            raise PermissionDenied("This marketplace identity is unavailable.")
        if BlockedUser.objects.filter(blocked_by__in=(request.user, product.seller), blocked_user__in=(request.user, product.seller)).exists():
            raise PermissionDenied("Messaging is unavailable because one account has blocked the other.")
        if conversation.context_type == Conversation.ContextType.GENERAL:
            conversation.context_type = Conversation.ContextType.MARKETPLACE
            conversation.context_id = product.pk
            conversation.context_title = product.title
            conversation.save(update_fields=("context_type", "context_id", "context_title", "updated_at"))
        elif conversation.context_type != Conversation.ContextType.MARKETPLACE or conversation.context_id != product.pk:
            raise ValidationError({"detail": "This conversation is linked to a different Tarmac feature."})
        from .serializers import create_participant_snapshot
        for person in participants:
            if not conversation.participant_snapshots.filter(user=person).exists():
                create_participant_snapshot(conversation, person, identity_type=("anonymous" if person.pk == product.seller_id and product.identity_type == Product.IdentityType.ANONYMOUS else "normal"))
        return Response(self.get_serializer(conversation).data)
    @action(detail=True, methods=["post"])
    @transaction.atomic
    def complete(self, request, pk=None):
        conversation = Conversation.objects.select_for_update().get(pk=self.get_object().pk)
        if conversation.context_type not in (Conversation.ContextType.GENERAL, Conversation.ContextType.MARKETPLACE, Conversation.ContextType.LOST_FOUND, Conversation.ContextType.JOBS):
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
            if conversation.context_type == Conversation.ContextType.LOST_FOUND:
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
        queryset = Message.objects.filter(conversation__participants=self.request.user).select_related("sender", "conversation").prefetch_related("attachments").order_by("-created_at", "-pk")
        conversation_id = self.request.query_params.get("conversation")
        return queryset.filter(conversation_id=conversation_id) if conversation_id else queryset
    def perform_create(self,serializer):
        convo=serializer.validated_data["conversation"]
        if not convo.participants.filter(pk=self.request.user.pk).exists(): raise PermissionDenied("You are not a participant in this conversation.")
        other_ids = convo.participants.exclude(pk=self.request.user.pk).values_list("pk", flat=True)
        if BlockedUser.objects.filter(blocked_by__in=[self.request.user.pk, *other_ids], blocked_user__in=[self.request.user.pk, *other_ids]).exists():
            raise PermissionDenied("Messaging is unavailable because one account has blocked the other.")
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

class BlockedUserViewSet(viewsets.ModelViewSet):
    serializer_class = BlockedUserSerializer
    permission_classes = [IsVerifiedStudent]
    http_method_names = ["get", "post", "delete", "head", "options"]
    def get_queryset(self):
        return BlockedUser.objects.filter(blocked_by=self.request.user).select_related("blocked_user")
    def perform_create(self, serializer):
        serializer.save()

@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def message_attachment_file(request, attachment_id):
    attachment = get_object_or_404(MessageAttachment.objects.select_related("message__conversation"), pk=attachment_id)
    if not request.user.is_staff and not attachment.message.conversation.participants.filter(pk=request.user.pk).exists():
        raise PermissionDenied("You are not a participant in this conversation.")
    content_type = attachment.content_type or mimetypes.guess_type(attachment.file.name)[0] or "application/octet-stream"
    return FileResponse(attachment.file.open("rb"), content_type=content_type)

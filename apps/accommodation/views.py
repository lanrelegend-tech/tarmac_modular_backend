from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from uuid import UUID

from apps.accounts.permissions import IsVerifiedStudent, is_student_feature_eligible
from apps.administration.models import AdminLog, PlatformSettings
from apps.administration.permissions import IsAdminOrAbove, IsModeratorOrAbove, PlatformFeatureEnabled
from apps.messaging.models import BlockedUser, Conversation
from apps.messaging.serializers import ConversationSerializer, create_participant_snapshot
from apps.notifications.services import notify_user
from apps.accounts.models import User
from .models import AccommodationListing, HostelAgentRequest
from .serializers import AccommodationListingSerializer, AdminAccommodationListingSerializer, HostelAgentRequestSerializer, AdminHostelAgentRequestSerializer


class AccommodationListingViewSet(viewsets.ModelViewSet):
    serializer_class = AccommodationListingSerializer
    permission_classes = (IsVerifiedStudent, PlatformFeatureEnabled)
    platform_feature = "accommodation_enabled"
    search_fields = ("title", "description", "location_area")
    filterset_fields = ("id", "public_id", "post_type", "accommodation_type", "rent_period")
    ordering_fields = ("created_at", "rent_amount", "budget_max")

    def retrieve(self, request, *args, **kwargs):
        key = kwargs.get(self.lookup_url_kwarg or self.lookup_field)
        try:
            public_id = UUID(str(key))
        except (TypeError, ValueError, AttributeError):
            return super().retrieve(request, *args, **kwargs)
        queryset = self.filter_queryset(self.get_queryset())
        instance = get_object_or_404(queryset, public_id=public_id)
        self.check_object_permissions(request, instance)
        return Response(self.get_serializer(instance).data)

    def get_queryset(self):
        queryset = AccommodationListing.objects.select_related("owner").prefetch_related("images")
        if self.request.query_params.get("mine") == "true":
            AccommodationListing.objects.filter(
                owner=self.request.user,
                status=AccommodationListing.Status.ACTIVE,
                expires_at__lte=timezone.now(),
            ).update(status=AccommodationListing.Status.EXPIRED)
            return queryset.filter(owner=self.request.user)
        campus = self.request.query_params.get("campus")
        if campus:
            queryset = queryset.filter(campus__iexact=campus.strip()[:120])
        platform = PlatformSettings.get_solo()
        eligible_owner = Q(owner__is_active=True, owner__is_suspended=False, owner__deleted_at__isnull=True)
        if platform.student_verification_enabled:
            eligible_owner &= Q(owner__is_student_verified=True)
        if platform.email_verification_required:
            eligible_owner &= Q(owner__email_verified=True)
        if self.action in ("retrieve", "update", "partial_update", "destroy"):
            visible = Q(status=AccommodationListing.Status.ACTIVE, expires_at__isnull=True) | Q(status=AccommodationListing.Status.ACTIVE, expires_at__gt=timezone.now())
            agent_policy = ~Q(post_type=AccommodationListing.PostType.OFFER_ACCOMMODATION) | Q(owner__is_hostel_agent=True)
            own_visible = Q(owner=self.request.user, status__in=(AccommodationListing.Status.PENDING_REVIEW, AccommodationListing.Status.REJECTED)) | Q(owner=self.request.user, status=AccommodationListing.Status.ACTIVE, expires_at__isnull=True) | Q(owner=self.request.user, status=AccommodationListing.Status.ACTIVE, expires_at__gt=timezone.now())
            return queryset.filter(eligible_owner & visible & agent_policy | own_visible)
        public_listings = ~Q(post_type=AccommodationListing.PostType.OFFER_ACCOMMODATION) | Q(owner__is_hostel_agent=True)
        return queryset.filter(eligible_owner, status=AccommodationListing.Status.ACTIVE).filter(public_listings).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))

    def perform_create(self, serializer):
        post_type = serializer.validated_data.get("post_type")
        if post_type == AccommodationListing.PostType.OFFER_ACCOMMODATION and (not self.request.user.is_hostel_agent or self.request.user.student_status != "student"):
            raise PermissionDenied("Only currently enrolled students approved as Hostel Agents can list accommodation for rent. Request the badge from your profile or contact Tarmac support.")
        pending_count = AccommodationListing.objects.filter(owner=self.request.user, status=AccommodationListing.Status.PENDING_REVIEW).count()
        if pending_count >= 3:
            raise ValidationError({"detail": "You already have 3 accommodation posts awaiting review. Wait for a decision before submitting another."})
        serializer.save(owner=self.request.user, status=AccommodationListing.Status.PENDING_REVIEW)

    def perform_update(self, serializer):
        if serializer.instance.owner_id != self.request.user.pk:
            raise PermissionDenied("You can only edit your own accommodation post.")
        if serializer.instance.status in (AccommodationListing.Status.REMOVED, AccommodationListing.Status.FILLED):
            raise ValidationError({"detail": "This post is closed and can no longer be edited."})
        next_post_type = serializer.validated_data.get("post_type", serializer.instance.post_type)
        if next_post_type == AccommodationListing.PostType.OFFER_ACCOMMODATION and (not self.request.user.is_hostel_agent or self.request.user.student_status != "student"):
            raise PermissionDenied("Only currently enrolled Hostel Agents can edit accommodation offer listings.")
        serializer.save(status=AccommodationListing.Status.PENDING_REVIEW, reviewer_note="", reviewed_by=None, reviewed_at=None, expires_at=None)

    def perform_destroy(self, instance):
        if instance.owner_id != self.request.user.pk:
            raise PermissionDenied("You can only remove your own accommodation post.")
        instance.status = AccommodationListing.Status.REMOVED
        instance.save(update_fields=("status", "updated_at"))

    @action(detail=True, methods=("post",))
    def filled(self, request, pk=None):
        listing = self.get_object()
        if listing.owner_id != request.user.pk:
            raise PermissionDenied("You can only close your own accommodation post.")
        if listing.status != AccommodationListing.Status.ACTIVE:
            raise ValidationError({"detail": "Only a published post can be marked as no longer available."})
        listing.status = AccommodationListing.Status.FILLED
        listing.save(update_fields=("status", "updated_at"))
        return Response({"status": listing.status})

    @action(detail=True, methods=("post",))
    @transaction.atomic
    def message(self, request, pk=None):
        listing = self.get_object()
        if listing.owner_id == request.user.pk:
            raise ValidationError({"detail": "You cannot message yourself about your accommodation post."})
        if listing.status != AccommodationListing.Status.ACTIVE or (listing.expires_at and listing.expires_at <= timezone.now()):
            raise ValidationError({"detail": "Only published accommodation posts can be contacted."})
        platform = PlatformSettings.get_solo()
        if not platform.messaging_enabled:
            raise PermissionDenied("Student messaging is temporarily unavailable.")
        if not is_student_feature_eligible(listing.owner):
            raise PermissionDenied("This student account is not available for messaging.")
        if BlockedUser.objects.filter(blocked_by__in=(request.user, listing.owner), blocked_user__in=(request.user, listing.owner)).exists():
            raise PermissionDenied("Messaging is unavailable because one account has blocked the other.")
        conversation = Conversation.objects.filter(
            context_type=Conversation.ContextType.ACCOMMODATION,
            context_id=listing.pk,
            participants=request.user,
        ).filter(participants=listing.owner).first()
        if conversation is None:
            conversation = Conversation.objects.create(
                context_type=Conversation.ContextType.ACCOMMODATION,
                context_id=listing.pk,
                context_title=f"Accommodation: {listing.title}"[:180],
            )
            conversation.participants.add(request.user, listing.owner)
            create_participant_snapshot(conversation, request.user)
            create_participant_snapshot(conversation, listing.owner)
        return Response(ConversationSerializer(conversation, context={"request": request}).data)


class AdminAccommodationListingViewSet(viewsets.ModelViewSet):
    serializer_class = AdminAccommodationListingSerializer
    permission_classes = (IsModeratorOrAbove,)
    http_method_names = ("get", "patch", "head", "options")
    search_fields = ("title", "description", "location_area", "owner__email", "owner__username", "owner__public_id")
    filterset_fields = ("status", "post_type", "accommodation_type")

    def get_queryset(self):
        AccommodationListing.objects.filter(
            status=AccommodationListing.Status.ACTIVE,
            expires_at__lte=timezone.now(),
        ).update(status=AccommodationListing.Status.EXPIRED)
        return AccommodationListing.objects.select_related("owner", "reviewed_by").prefetch_related("images")

    @transaction.atomic
    def perform_update(self, serializer):
        listing = AccommodationListing.objects.select_for_update().get(pk=serializer.instance.pk)
        next_status = serializer.validated_data.get("status", listing.status)
        valid = {choice for choice, _ in AccommodationListing.Status.choices}
        if next_status not in valid:
            raise ValidationError({"status": "Choose a valid accommodation review status."})
        if next_status == AccommodationListing.Status.REJECTED and not serializer.validated_data.get("reviewer_note", listing.reviewer_note).strip():
            raise ValidationError({"reviewer_note": "Add a clear reason when rejecting a post."})
        old_status = listing.status
        for key, value in serializer.validated_data.items():
            setattr(listing, key, value)
        if next_status in (AccommodationListing.Status.ACTIVE, AccommodationListing.Status.REJECTED, AccommodationListing.Status.REMOVED):
            listing.reviewed_by = self.request.user
            listing.reviewed_at = timezone.now()
        if next_status == AccommodationListing.Status.ACTIVE:
            listing.expires_at = timezone.now() + timedelta(days=PlatformSettings.get_solo().accommodation_listing_duration_days)
        listing.save()
        serializer.instance = listing
        AdminLog.objects.create(
            actor=self.request.user,
            action="accommodation_listing_reviewed",
            target_type="AccommodationListing",
            target_id=str(listing.pk),
            metadata={"previous_status": old_status, "status": listing.status, "owner_id": listing.owner_id},
        )
        if listing.status != old_status:
            subject = "Your accommodation post was approved" if listing.status == AccommodationListing.Status.ACTIVE else "Your accommodation post needs changes" if listing.status == AccommodationListing.Status.REJECTED else "Your accommodation post was removed"
            body = f"{listing.title} is now published to verified students." if listing.status == AccommodationListing.Status.ACTIVE else listing.reviewer_note or "Your post is no longer visible. Sign in to review it or contact Tarmac support."
            notify_user(listing.owner, subject, body, {"kind": "accommodation_review", "listing_id": listing.pk, "status": listing.status, "notification_category": "accommodation", "link": "/accommodation"}, f"accommodation-review:{listing.pk}:{listing.status}:{listing.updated_at.timestamp()}", dispatch_email=True)


class HostelAgentRequestViewSet(viewsets.ModelViewSet):
    serializer_class = HostelAgentRequestSerializer
    permission_classes = (IsVerifiedStudent, PlatformFeatureEnabled)
    platform_feature = "accommodation_enabled"
    http_method_names = ("get", "post", "head", "options")

    def get_queryset(self):
        return HostelAgentRequest.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        user = self.request.user
        if user.student_status != "student" or not user.is_student_verified:
            raise PermissionDenied("Only currently verified students can request a Hostel Agent badge.")
        if user.is_hostel_agent:
            raise ValidationError({"detail": "Your account already has the Hostel Agent badge."})
        if HostelAgentRequest.objects.filter(user=user, status=HostelAgentRequest.Status.PENDING).exists():
            raise ValidationError({"detail": "You already have a Hostel Agent request awaiting review."})
        request_record = serializer.save(user=user, status=HostelAgentRequest.Status.PENDING)
        AdminLog.objects.create(actor=user, action="hostel_agent_badge_requested", target_type="HostelAgentRequest", target_id=str(request_record.pk), metadata={"user_id": user.pk})
        for administrator in User.objects.filter(is_staff=True, is_active=True, is_suspended=False):
            notify_user(administrator, "Hostel Agent badge request", f"{user.get_full_name().strip() or user.username} requested permission to list accommodation.", {"kind": "accommodation_agent_request", "notification_category": "accommodation", "request_id": request_record.pk, "link": "/admin/accommodation"}, f"hostel-agent-request:{request_record.pk}:{administrator.pk}")


class AdminHostelAgentRequestViewSet(viewsets.ModelViewSet):
    serializer_class = AdminHostelAgentRequestSerializer
    permission_classes = (IsAdminOrAbove,)
    http_method_names = ("get", "patch", "head", "options")
    search_fields = ("user__username", "user__first_name", "user__last_name", "user__email", "user__public_id", "user__campus")
    filterset_fields = ("status",)

    def get_queryset(self):
        return HostelAgentRequest.objects.select_related("user", "reviewed_by")

    @transaction.atomic
    def perform_update(self, serializer):
        request_record = HostelAgentRequest.objects.select_for_update().select_related("user").get(pk=serializer.instance.pk)
        next_status = serializer.validated_data.get("status", request_record.status)
        note = str(serializer.validated_data.get("admin_note", request_record.admin_note)).strip()
        if next_status not in HostelAgentRequest.Status.values:
            raise ValidationError({"status": "Choose a valid Hostel Agent request status."})
        if next_status in (HostelAgentRequest.Status.REJECTED, HostelAgentRequest.Status.REVOKED) and len(note) < 5:
            raise ValidationError({"admin_note": "Give the student a clear reason for rejecting the request or revoking the badge."})
        if next_status == HostelAgentRequest.Status.APPROVED:
            user = request_record.user
            if not (user.is_active and not user.is_suspended and user.deleted_at is None and user.is_student_verified and user.student_status == "student"):
                raise ValidationError({"status": "Only active, currently verified students can receive a Hostel Agent badge."})
            user.is_hostel_agent = True
            user.save(update_fields=("is_hostel_agent",))
        elif next_status in (HostelAgentRequest.Status.REJECTED, HostelAgentRequest.Status.REVOKED):
            request_record.user.is_hostel_agent = False
            request_record.user.save(update_fields=("is_hostel_agent",))
        previous_status = request_record.status
        request_record.status = next_status
        request_record.admin_note = note
        request_record.reviewed_by = self.request.user
        request_record.reviewed_at = timezone.now()
        request_record.save(update_fields=("status", "admin_note", "reviewed_by", "reviewed_at", "updated_at"))
        serializer.instance = request_record
        AdminLog.objects.create(actor=self.request.user, action=f"hostel_agent_request_{next_status}", target_type="HostelAgentRequest", target_id=str(request_record.pk), metadata={"user_id": request_record.user_id, "previous_status": previous_status, "note": note})
        if previous_status != next_status:
            subject = "Your Hostel Agent badge was approved" if next_status == HostelAgentRequest.Status.APPROVED else "Your Hostel Agent badge request was not approved" if next_status == HostelAgentRequest.Status.REJECTED else "Your Hostel Agent badge was revoked"
            body = "Your Hostel Agent badge is now active. You can list accommodation offers on Tarmac." if next_status == HostelAgentRequest.Status.APPROVED else note
            notify_user(request_record.user, subject, body, {"kind": "accommodation_agent_review", "notification_category": "accommodation", "status": next_status, "admin_note": note, "link": "/accommodation"}, f"hostel-agent-review:{request_record.pk}:{next_status}:{timezone.now().timestamp()}", dispatch_email=True)

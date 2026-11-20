import mimetypes
from django.db import models, transaction
from django.db.models import Count
from django.http import FileResponse
from rest_framework import viewsets, permissions
from rest_framework.exceptions import PermissionDenied
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import status
from apps.accounts.permissions import IsVerifiedStudent, is_student_feature_eligible
from apps.administration.permissions import PlatformFeatureEnabled
from .models import LostFoundReport,ItemMatch,ItemMatchReview,ItemClaim
from .serializers import LostFoundReportSerializer,ItemMatchSerializer,ItemClaimSerializer
from .services import create_possible_matches
from apps.notifications.services import notify_user
from apps.messaging.models import Conversation
from apps.messaging.serializers import ConversationSerializer
class LostFoundReportViewSet(viewsets.ModelViewSet):
    platform_feature = "lost_found_enabled"
    serializer_class=LostFoundReportSerializer; permission_classes=[IsVerifiedStudent, PlatformFeatureEnabled]
    search_fields = ["title", "description", "location", "colour", "identifying_details"]
    filterset_fields = ["report_type", "category", "status"]
    def get_queryset(self): return LostFoundReport.objects.filter(is_active=True, status__in=(LostFoundReport.Status.OPEN, LostFoundReport.Status.MATCHED))
    def perform_create(self,serializer):
        report=serializer.save(reporter=self.request.user); create_possible_matches(report)
    def perform_update(self,serializer):
        if serializer.instance.reporter_id!=self.request.user.id: raise PermissionDenied("You can only update your own report.")
        report = serializer.save()
        create_possible_matches(report)
    def perform_destroy(self, instance):
        if instance.reporter_id != self.request.user.id:
            raise PermissionDenied("You can only delete your own report.")
        instance.delete()
    @action(detail=True, methods=["get"], url_path="image-file")
    def image_file(self, request, pk=None):
        report = self.get_object()
        if not report.image:
            from rest_framework.exceptions import NotFound
            raise NotFound("This report has no image.")
        content_type = mimetypes.guess_type(report.image.name)[0] or "application/octet-stream"
        return FileResponse(report.image.open("rb"), content_type=content_type)
    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        report = self.get_object()
        if report.reporter_id != request.user.id:
            raise PermissionDenied("You can only close your own report.")
        report.status = LostFoundReport.Status.CLOSED
        report.is_active = False
        report.save(update_fields=("status", "is_active"))
        return Response({"status": report.status}, status=status.HTTP_200_OK)
    @action(detail=True, methods=["post"])
    def returned(self, request, pk=None):
        report = self.get_object()
        if report.reporter_id != request.user.id:
            raise PermissionDenied("Only the report owner can confirm that the item was returned.")
        if report.status == LostFoundReport.Status.RETURNED:
            return Response({"status": report.status})
        report.status = LostFoundReport.Status.RETURNED
        report.is_active = False
        report.save(update_fields=("status", "is_active"))
        matches = ItemMatch.objects.filter(is_dismissed=False).filter(
            models.Q(lost_report=report) | models.Q(found_report=report)
        ).select_related("lost_report__reporter", "found_report__reporter")
        notified = set()
        for match in matches:
            LostFoundReport.objects.filter(pk__in=(match.lost_report_id, match.found_report_id)).update(status=LostFoundReport.Status.RETURNED, is_active=False)
            for conversation in Conversation.objects.filter(context_type=Conversation.ContextType.LOST_FOUND, context_id=match.pk):
                conversation.status = Conversation.Status.RESOLVED
                conversation.completed_at = timezone.now()
                conversation.completed_by.add(match.lost_report.reporter, match.found_report.reporter)
                conversation.save(update_fields=("status", "completed_at", "updated_at"))
            for other in (match.lost_report.reporter, match.found_report.reporter):
                if other.pk not in notified:
                    notify_user(other, "Lost-and-found item marked returned", f'"{report.title}" has been marked returned. The related lost-and-found conversation is complete.',
                                {"kind":"lost_found_returned", "report_id":report.pk}, f"lost-found-returned:{report.pk}:{other.pk}")
                    notified.add(other.pk)
        return Response({"status": report.status})
class ItemMatchViewSet(viewsets.ReadOnlyModelViewSet):
    platform_feature = "lost_found_enabled"
    serializer_class=ItemMatchSerializer; permission_classes=[IsVerifiedStudent, PlatformFeatureEnabled]
    def get_queryset(self): return (ItemMatch.objects.filter(lost_report__reporter=self.request.user)|ItemMatch.objects.filter(found_report__reporter=self.request.user)).filter(lost_report__is_active=True, found_report__is_active=True, is_dismissed=False)
    @action(detail=True, methods=["post"])
    @transaction.atomic
    def message(self, request, pk=None):
        match = self.get_object()
        other = match.found_report.reporter if request.user.pk == match.lost_report.reporter_id else match.lost_report.reporter
        if other.pk == request.user.pk or not is_student_feature_eligible(other):
            raise PermissionDenied("The other student is not available for messaging.")
        title = f"Lost and found match: {match.lost_report.title} / {match.found_report.title}"[:180]
        conversation = Conversation.objects.annotate(participant_count=Count("participants", distinct=True)).filter(
            context_type=Conversation.ContextType.LOST_FOUND, context_id=match.pk, participant_count=2,
            participants=request.user,
        ).filter(participants=other).first()
        if conversation is None:
            conversation = Conversation.objects.create(
                context_type=Conversation.ContextType.LOST_FOUND,
                context_id=match.pk,
                context_title=title,
            )
            conversation.participants.add(request.user, other)
        return Response(ConversationSerializer(conversation, context={"request": request}).data, status=status.HTTP_200_OK)
    @action(detail=True, methods=["post"])
    def review(self, request, pk=None):
        match = self.get_object()
        decision = request.data.get("decision")
        valid = [choice for choice, _label in ItemMatchReview.Decision.choices]
        if decision not in valid:
            return Response({"decision": [f"Choose one of: {', '.join(valid)}."]}, status=status.HTTP_400_BAD_REQUEST)
        review, _created = ItemMatchReview.objects.update_or_create(
            match=match,
            user=request.user,
            defaults={"decision": decision},
        )
        if decision == ItemMatchReview.Decision.POSSIBLE_MATCH:
            other = match.found_report.reporter if request.user.pk == match.lost_report.reporter_id else match.lost_report.reporter
            if other.pk != request.user.pk:
                notify_user(other, "A student confirmed a possible item match", f'Another student confirmed a possible match for "{match.lost_report.title}".',
                            {"kind":"lost_found_match_response", "match_id":match.pk}, f"lost-found-match-response:{match.pk}:{other.pk}")
        return Response({"match_id": match.pk, "decision": review.decision}, status=status.HTTP_200_OK)

class ItemClaimViewSet(viewsets.ModelViewSet):
    serializer_class = ItemClaimSerializer
    permission_classes = [IsVerifiedStudent, PlatformFeatureEnabled]
    platform_feature = "lost_found_enabled"
    http_method_names = ["get", "post", "head", "options"]
    def get_queryset(self): return ItemClaim.objects.filter(models.Q(claimant=self.request.user) | models.Q(report__reporter=self.request.user)).select_related("report", "claimant")
    def perform_create(self, serializer):
        report = serializer.validated_data["report"]
        if report.reporter_id == self.request.user.pk:
            raise PermissionDenied("You cannot claim your own found-item report.")
        claim = serializer.save(claimant=self.request.user, evidence_name=serializer.validated_data.get("evidence").name if serializer.validated_data.get("evidence") else "")
        notify_user(report.reporter, "A student submitted an item claim", f'A student has submitted a claim for "{report.title}". School staff will review it.', {"kind":"item_claim", "claim_id":claim.pk}, f"item-claim:{claim.pk}:finder")
    @action(detail=True, methods=["get"], url_path="evidence")
    def evidence_file(self, request, pk=None):
        claim = self.get_object()
        if not claim.evidence: raise PermissionDenied("No evidence is attached to this claim.")
        content_type = mimetypes.guess_type(claim.evidence.name)[0] or "application/octet-stream"
        return FileResponse(claim.evidence.open("rb"), content_type=content_type)

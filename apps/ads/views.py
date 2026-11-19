import mimetypes

from django.http import FileResponse
from django.db.models import F
from django.shortcuts import redirect
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied

from apps.accounts.permissions import IsVerifiedStudent
from apps.administration.models import PlatformSettings
from apps.administration.permissions import PlatformFeatureEnabled
from .models import AdCampaign
from .serializers import AdCampaignSerializer


class AdCampaignViewSet(viewsets.ModelViewSet):
    platform_feature = "ads_enabled"
    serializer_class = AdCampaignSerializer
    permission_classes = (IsVerifiedStudent, PlatformFeatureEnabled)

    def get_queryset(self):
        now = timezone.now()
        active = AdCampaign.objects.filter(status=AdCampaign.Status.ACTIVE).filter(
            models_q_start(now), models_q_end(now)
        )
        return active | AdCampaign.objects.filter(advertiser=self.request.user)

    def perform_create(self, serializer):
        platform = PlatformSettings.get_solo()
        needs_payment = platform.ad_fee_enabled and platform.paystack_enabled
        initial_status = AdCampaign.Status.PENDING_PAYMENT if needs_payment else AdCampaign.Status.PENDING_REVIEW
        serializer.save(advertiser=self.request.user, status=initial_status)

    def perform_update(self, serializer):
        if serializer.instance.advertiser_id != self.request.user.id:
            raise PermissionDenied("You can only update your own ad campaign.")
        current_status = serializer.instance.status
        review_status = AdCampaign.Status.PENDING_PAYMENT if current_status == AdCampaign.Status.PENDING_PAYMENT else AdCampaign.Status.PENDING_REVIEW
        serializer.save(status=review_status, starts_at=None, ends_at=None)

    def perform_destroy(self, instance):
        if instance.advertiser_id != self.request.user.id:
            raise PermissionDenied("You can only delete your own ad campaign.")
        instance.delete()

    @action(detail=True, methods=("get",), url_path="image")
    def image_file(self, request, pk=None):
        campaign = self.get_object()
        content_type = mimetypes.guess_type(campaign.image.name)[0] or "application/octet-stream"
        return FileResponse(campaign.image.open("rb"), content_type=content_type)

    @action(detail=True, methods=("get",), url_path="click")
    def click(self, request, pk=None):
        campaign = self.get_object()
        if campaign.status != AdCampaign.Status.ACTIVE:
            raise PermissionDenied("This ad is not active.")
        AdCampaign.objects.filter(pk=campaign.pk, status=AdCampaign.Status.ACTIVE).update(click_count=F("click_count") + 1)
        return redirect(campaign.target_url)


def models_q_start(now):
    from django.db.models import Q
    return Q(starts_at__isnull=True) | Q(starts_at__lte=now)


def models_q_end(now):
    from django.db.models import Q
    return Q(ends_at__isnull=True) | Q(ends_at__gt=now)

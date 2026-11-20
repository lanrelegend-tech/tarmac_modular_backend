import mimetypes

from django.http import FileResponse
from django.db.models import F, Q, CharField, Value
from django.db.models.functions import Cast
from django.db.models.lookups import IContains
from django.shortcuts import redirect
from django.utils import timezone
from datetime import timedelta

from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from apps.accounts.permissions import IsVerifiedStudent, is_student_feature_eligible
from apps.administration.models import PlatformSettings
from apps.administration.permissions import PlatformFeatureEnabled
from .models import AdCampaign, AdImpression
from .serializers import AdCampaignSerializer


class AdCampaignViewSet(viewsets.ModelViewSet):
    platform_feature = "ads_enabled"
    serializer_class = AdCampaignSerializer
    permission_classes = (IsVerifiedStudent, PlatformFeatureEnabled)

    def get_permissions(self):
        if getattr(self, "action", None) in ("list", "retrieve", "image_file", "click"):
            return [permissions.AllowAny(), PlatformFeatureEnabled()]
        if getattr(self, "action", None) in ("approve", "reject", "pause"):
            return [permissions.IsAdminUser()]
        if self.request.user and self.request.user.is_staff:
            return [permissions.IsAdminUser()]
        return [IsVerifiedStudent(), PlatformFeatureEnabled()]

    def get_queryset(self):
        if self.request.user.is_staff:
            return AdCampaign.objects.select_related("advertiser").all()
        now = timezone.now()
        active = AdCampaign.objects.filter(status=AdCampaign.Status.ACTIVE).filter(
            models_q_start(now), models_q_end(now)
        )
        surface = self.request.query_params.get("surface")
        platform = PlatformSettings.get_solo()
        surface_flags = {
            "home": (platform.homepage_ads_enabled, "homepage"),
            "homepage": (platform.homepage_ads_enabled, "homepage"),
            "announcements": (platform.homepage_ads_enabled, "announcements"),
            "marketplace": (platform.marketplace_ads_enabled, "marketplace"),
            "lost-found": (platform.lost_found_enabled, "lost_found"),
            "lost_found": (platform.lost_found_enabled, "lost_found"),
        }
        if surface and surface not in surface_flags:
            return AdCampaign.objects.none()
        if surface in surface_flags:
            is_enabled, page_value = surface_flags[surface]
            if not is_enabled:
                return AdCampaign.objects.none()
            # Cast JSON to text so placement filtering works on SQLite in local
            # development as well as PostgreSQL in production.
            page_text = Cast("pages", output_field=CharField())
            active = active.filter(Q(IContains(page_text, Value(f'"{page_value}"'))))
            return active
        if self.request.user.is_authenticated:
            return active | AdCampaign.objects.filter(advertiser=self.request.user)
        return active

    def perform_create(self, serializer):
        platform = PlatformSettings.get_solo()
        if self.request.user.is_staff:
            from apps.administration.models import AdminLog
            from django.utils import timezone as django_timezone
            package = serializer.validated_data.get("package")
            start = serializer.validated_data.get("starts_at") or django_timezone.now()
            duration_days = package.duration_days if package else 30
            end = serializer.validated_data.get("ends_at") or start + timedelta(days=duration_days)
            campaign = serializer.save(status=AdCampaign.Status.ACTIVE, starts_at=start, ends_at=end, duration_days=duration_days, price_kobo=serializer.validated_data.get("price_kobo", package.price_kobo if package else 0))
            AdminLog.objects.create(actor=self.request.user, action="admin_ad_campaign_created", target_type="AdCampaign", target_id=str(campaign.pk), metadata={"advertiser_id": campaign.advertiser_id, "price_kobo": campaign.price_kobo})
            return
        package = serializer.validated_data.get("package")
        if package is None:
            raise ValidationError({"package": "Choose an active advertising package."})
        needs_payment = platform.ad_fee_enabled and platform.paystack_enabled and package.price_kobo > 0
        initial_status = AdCampaign.Status.PENDING_PAYMENT if needs_payment else AdCampaign.Status.PENDING_REVIEW
        serializer.save(advertiser=self.request.user, status=initial_status, price_kobo=package.price_kobo, duration_days=package.duration_days)

    def perform_update(self, serializer):
        if not self.request.user.is_staff and serializer.instance.advertiser_id != self.request.user.id:
            raise PermissionDenied("You can only update your own ad campaign.")
        current_status = serializer.instance.status
        if self.request.user.is_staff:
            from apps.administration.models import AdminLog
            campaign = serializer.save(status=current_status)
            AdminLog.objects.create(actor=self.request.user, action="admin_ad_campaign_updated", target_type="AdCampaign", target_id=str(campaign.pk), metadata={})
            return
        review_status = AdCampaign.Status.PENDING_PAYMENT if current_status == AdCampaign.Status.PENDING_PAYMENT else AdCampaign.Status.PENDING_REVIEW
        package = serializer.validated_data.get("package", serializer.instance.package)
        serializer.save(status=review_status, starts_at=None, ends_at=None, price_kobo=package.price_kobo if package else 0)

    def perform_destroy(self, instance):
        if self.request.user.is_staff:
            from apps.administration.models import AdminLog
            AdminLog.objects.create(actor=self.request.user, action="ad_campaign_removed", target_type="AdCampaign", target_id=str(instance.pk), metadata={})
        elif instance.advertiser_id != self.request.user.id:
            raise PermissionDenied("You can only delete your own ad campaign.")
        instance.delete()


    @action(detail=True, methods=("post",), permission_classes=(permissions.IsAdminUser,))
    def approve(self, request, pk=None):
        campaign = self.get_object()
        platform = PlatformSettings.get_solo()
        from apps.payments.models import Payment
        payment_required = platform.ad_fee_enabled and platform.paystack_enabled and campaign.price_kobo > 0
        paid = Payment.objects.filter(advertisement=campaign, status=Payment.Status.SUCCESS, amount_kobo=campaign.price_kobo).exists()
        if campaign.status != AdCampaign.Status.PENDING_REVIEW:
            raise ValidationError({"status": "Only campaigns awaiting review can be approved."})
        if payment_required and not paid:
            raise ValidationError({"payment": "A successful payment is required before approval."})
        if not is_student_feature_eligible(campaign.advertiser):
            raise ValidationError({"advertiser": "The campaign owner must have an active student account and verified email."})
        campaign.status = AdCampaign.Status.ACTIVE
        campaign.starts_at = timezone.now()
        campaign.ends_at = campaign.starts_at + timedelta(days=campaign.duration_days)
        campaign.save(update_fields=("status", "starts_at", "ends_at", "updated_at"))
        if campaign.package_id:
            successful_payment = Payment.objects.filter(advertisement=campaign, status=Payment.Status.SUCCESS).order_by("-paid_at").first()
            from apps.administration.models import StudentSubscription
            StudentSubscription.objects.get_or_create(ad_campaign=campaign, defaults={"user": campaign.advertiser, "plan": campaign.package, "amount_kobo": successful_payment.amount_kobo if successful_payment else 0, "payment_reference": successful_payment.reference if successful_payment else None, "starts_at": campaign.starts_at, "ends_at": campaign.ends_at})
        from apps.administration.models import AdminLog
        from apps.notifications.services import notify_user
        AdminLog.objects.create(actor=request.user, action="ad_campaign_approved", target_type="AdCampaign", target_id=str(campaign.pk), metadata={})
        notify_user(campaign.advertiser, "Ad campaign approved", f"Your campaign '{campaign.title}' is now active.", {"kind": "ad_approved"}, f"ad-approved:{campaign.pk}")
        return Response({"status": campaign.status})

    @action(detail=True, methods=("post",), permission_classes=(permissions.IsAdminUser,))
    def reject(self, request, pk=None):
        campaign = self.get_object()
        if campaign.status == AdCampaign.Status.ACTIVE:
            raise ValidationError({"status": "Pause an active campaign before rejecting it."})
        campaign.status = AdCampaign.Status.REJECTED
        campaign.save(update_fields=("status", "updated_at"))
        from apps.administration.models import AdminLog
        AdminLog.objects.create(actor=request.user, action="ad_campaign_rejected", target_type="AdCampaign", target_id=str(campaign.pk), metadata={})
        return Response({"status": campaign.status})

    @action(detail=True, methods=("post",), permission_classes=(permissions.IsAdminUser,))
    def pause(self, request, pk=None):
        campaign = self.get_object()
        if campaign.status != AdCampaign.Status.ACTIVE:
            raise ValidationError({"status": "Only active campaigns can be paused."})
        campaign.status = AdCampaign.Status.PAUSED
        campaign.save(update_fields=("status", "updated_at"))
        from apps.administration.models import AdminLog
        AdminLog.objects.create(actor=request.user, action="ad_campaign_paused", target_type="AdCampaign", target_id=str(campaign.pk), metadata={})
        return Response({"status": campaign.status})

    @action(detail=True, methods=("post",), permission_classes=(permissions.IsAdminUser,))
    def activate(self, request, pk=None):
        campaign = self.get_object()
        if campaign.status != AdCampaign.Status.PAUSED:
            raise ValidationError({"status": "Only paused campaigns can be reactivated."})
        now = timezone.now()
        if campaign.ends_at and campaign.ends_at <= now:
            campaign.ends_at = now + timedelta(days=campaign.duration_days)
        campaign.status = AdCampaign.Status.ACTIVE
        campaign.starts_at = now
        campaign.save(update_fields=("status", "starts_at", "ends_at", "updated_at"))
        from apps.administration.models import AdminLog
        AdminLog.objects.create(actor=request.user, action="ad_campaign_reactivated", target_type="AdCampaign", target_id=str(campaign.pk), metadata={})
        return Response({"status": campaign.status})

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
        return redirect(campaign.target_url or "/")

    @action(detail=True, methods=("post",), url_path="impression")
    def impression(self, request, pk=None):
        campaign = self.get_object()
        if campaign.status != AdCampaign.Status.ACTIVE:
            raise PermissionDenied("This ad is not active.")
        impression, created = AdImpression.objects.get_or_create(campaign=campaign, user=request.user, day=timezone.localdate())
        if created:
            AdCampaign.objects.filter(pk=campaign.pk, status=AdCampaign.Status.ACTIVE).update(impression_count=F("impression_count") + 1)
        return Response({"tracked": created})


def models_q_start(now):
    from django.db.models import Q
    return Q(starts_at__isnull=True) | Q(starts_at__lte=now)


def models_q_end(now):
    from django.db.models import Q
    return Q(ends_at__isnull=True) | Q(ends_at__gt=now)

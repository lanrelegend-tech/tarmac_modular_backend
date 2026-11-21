import mimetypes
import hashlib
import secrets
import os
from datetime import datetime, timedelta

from django.http import FileResponse
from django.db.models import F, Q, CharField, Value
from django.db import transaction
from django.db.models.functions import Cast
from django.db.models.lookups import IContains
from django.shortcuts import redirect, get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date

from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from apps.accounts.permissions import IsVerifiedStudent, is_student_feature_eligible
from apps.administration.models import PlatformSettings
from apps.administration.permissions import PlatformFeatureEnabled
from .models import AdAdvertiser, AdCampaign, AdImpression, AdPlacement, AdSlotHold, AdWaitlistEntry
from .serializers import AdCampaignSerializer, AdPlacementSerializer, AdWaitlistEntrySerializer
from .services import get_slot_availability, local_date_window
from .throttles import PublicAdManageThrottle, PublicAdSubmissionThrottle


def _token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _dates_from_query(query_params):
    start_date = parse_date(str(query_params.get("start_date", "")))
    end_date = parse_date(str(query_params.get("end_date", "")))
    if not start_date or not end_date or end_date < start_date:
        raise ValidationError({"dates": "Choose valid start and end dates."})
    if start_date < timezone.localdate() or (end_date - start_date).days > 365:
        raise ValidationError({"dates": "Start date must be today or later, and campaigns cannot exceed one year."})
    return local_date_window(start_date, end_date), start_date, end_date


def _default_placements_for_pages(pages):
    keys = {
        "homepage": "homepage.main_banner",
        "marketplace": "marketplace.banner",
        "announcements": "announcements.banner",
        "jobs": "jobs.banner",
        "lost_found": "lost_found.banner",
    }
    return list(AdPlacement.objects.filter(key__in=[keys[page] for page in pages if page in keys], is_active=True).order_by("sort_order", "pk"))


class AdPlacementViewSet(viewsets.ModelViewSet):
    platform_feature = "ads_enabled"
    serializer_class = AdPlacementSerializer
    queryset = AdPlacement.objects.all()
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset if self.request.user.is_staff else queryset.filter(is_active=True)

    def get_throttles(self):
        if getattr(self, "action", None) == "waitlist":
            return [PublicAdSubmissionThrottle()]
        return super().get_throttles()

    def get_permissions(self):
        if self.action in ("list", "retrieve", "availability", "waitlist"):
            return [permissions.AllowAny(), PlatformFeatureEnabled()]
        return [permissions.IsAdminUser()]

    @action(detail=True, methods=["get"])
    def availability(self, request, pk=None):
        placement = self.get_object()
        (starts_at, ends_at), start_date, end_date = _dates_from_query(request.query_params)
        availability = get_slot_availability(placement, starts_at, ends_at)
        if availability["remaining"]:
            _offer_next_waitlisted(placement, starts_at, ends_at)
        return Response({"placement": placement.key, "start_date": start_date, "end_date": end_date, **availability})

    @action(detail=True, methods=["post"])
    def waitlist(self, request, pk=None):
        placement = self.get_object()
        (starts_at, ends_at), start_date, end_date = _dates_from_query(request.data)
        availability = get_slot_availability(placement, starts_at, ends_at)
        if availability["remaining"]:
            return Response({"detail": "This placement currently has availability for those dates. Continue with an ad booking instead."}, status=status.HTTP_409_CONFLICT)
        business_name = str(request.data.get("business_name", "")).strip()[:160]
        contact_name = str(request.data.get("contact_name", "")).strip()[:120]
        email = str(request.data.get("email", "")).strip().lower()
        phone = str(request.data.get("phone", "")).strip()[:32]
        if len(business_name) < 2 or len(contact_name) < 2 or len(phone) < 7 or "@" not in email:
            raise ValidationError({"detail": "Provide a business name, contact name, valid email and phone number."})
        advertiser = AdAdvertiser.objects.filter(email__iexact=email).first()
        if advertiser is None:
            advertiser = AdAdvertiser.objects.create(business_name=business_name, contact_name=contact_name, email=email, phone=phone)
        else:
            advertiser.business_name = business_name
            advertiser.contact_name = contact_name
            advertiser.phone = phone
            advertiser.save(update_fields=("business_name", "contact_name", "phone", "updated_at"))
        entry, created = AdWaitlistEntry.objects.get_or_create(advertiser=advertiser, placement=placement, starts_at=starts_at, ends_at=ends_at)
        if entry.status == AdWaitlistEntry.Status.CANCELLED:
            entry.status = AdWaitlistEntry.Status.WAITING
            entry.save(update_fields=("status", "updated_at"))
        return Response({"id": entry.pk, "status": entry.status, "created": created, "placement": placement.position, "start_date": start_date, "end_date": end_date}, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class AdminAdWaitlistViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = AdWaitlistEntrySerializer
    permission_classes = [permissions.IsAdminUser]

    def get_queryset(self):
        queryset = AdWaitlistEntry.objects.select_related("advertiser", "placement").all()
        status_filter = self.request.query_params.get("status")
        return queryset.filter(status=status_filter) if status_filter in AdWaitlistEntry.Status.values else queryset

    @action(detail=True, methods=["post"], url_path="notify")
    def notify(self, request, pk=None):
        entry = self.get_object()
        if not entry.placement.is_active:
            raise ValidationError({"placement": "This placement is disabled and cannot accept bookings."})
        if entry.status not in (AdWaitlistEntry.Status.WAITING, AdWaitlistEntry.Status.OFFERED):
            raise ValidationError({"status": "Only waiting advertisers can be contacted."})
        if get_slot_availability(entry.placement, entry.starts_at, entry.ends_at)["remaining"] <= 0:
            raise ValidationError({"availability": "This placement is still fully booked for those dates."})
        _send_waitlist_offer(entry, request.user)
        return Response({"status": entry.status, "offer_expires_at": entry.offer_expires_at})


class AdminAdPlacementViewSet(viewsets.ModelViewSet):
    serializer_class = AdPlacementSerializer
    queryset = AdPlacement.objects.all()
    permission_classes = [permissions.IsAdminUser]
    http_method_names = ["get", "patch", "head", "options"]

    @action(detail=True, methods=["get"])
    def availability(self, request, pk=None):
        placement = self.get_object()
        (starts_at, ends_at), start_date, end_date = _dates_from_query(request.query_params)
        return Response({"placement": placement.key, "start_date": start_date, "end_date": end_date, **get_slot_availability(placement, starts_at, ends_at)})

    def perform_update(self, serializer):
        placement = serializer.save()
        from apps.administration.models import AdminLog
        AdminLog.objects.create(actor=self.request.user, action="ad_placement_updated", target_type="AdPlacement", target_id=str(placement.pk), metadata={"key": placement.key, "max_slots": placement.max_slots, "is_active": placement.is_active, "is_exclusive": placement.is_exclusive})


def _send_waitlist_offer(entry, actor=None):
    from apps.notifications.email import send_tarmac_email
    from apps.administration.models import AdminLog

    token = secrets.token_urlsafe(32)
    entry.status = AdWaitlistEntry.Status.OFFERED
    entry.offer_token_hash = _token_hash(token)
    entry.offer_expires_at = timezone.now() + timedelta(hours=24)
    entry.save(update_fields=("status", "offer_token_hash", "offer_expires_at", "updated_at"))
    origin = os.getenv("FRONTEND_ORIGIN", "http://localhost:3000").split(",")[0].rstrip("/")
    start_date = timezone.localtime(entry.starts_at).date().isoformat()
    end_date = (timezone.localtime(entry.ends_at) - timedelta(seconds=1)).date().isoformat()
    duration_days = (timezone.localtime(entry.ends_at).date() - timezone.localtime(entry.starts_at).date()).days
    action_url = f"{origin}/advertise?placement={entry.placement.key}&start_date={start_date}&end_date={end_date}&duration_days={duration_days}&waitlist={token}"
    send_tarmac_email(
        subject="An advertising slot is available on Tarmac",
        text_body=f"A slot is available for {entry.placement.position} from {start_date} to {end_date}. You have 24 hours to submit your campaign request. The slot is not held until your booking is confirmed.",
        recipient=entry.advertiser.email,
        recipient_name=entry.advertiser.contact_name,
        action_url=action_url,
        action_label="Request this ad slot",
    )
    if actor:
        AdminLog.objects.create(actor=actor, action="ad_waitlist_offer_sent", target_type="AdWaitlistEntry", target_id=str(entry.pk), metadata={"placement": entry.placement.key})


def _offer_next_waitlisted(placement, starts_at, ends_at):
    if not placement.is_active:
        return
    active_offer = AdWaitlistEntry.objects.filter(
        placement=placement,
        status=AdWaitlistEntry.Status.OFFERED,
        offer_expires_at__gt=timezone.now(),
        starts_at__lt=ends_at,
        ends_at__gt=starts_at,
    ).exists()
    if active_offer:
        return
    entry = AdWaitlistEntry.objects.filter(
        placement=placement,
        status=AdWaitlistEntry.Status.WAITING,
        starts_at__lt=ends_at,
        ends_at__gt=starts_at,
    ).order_by("created_at", "pk").first()
    if entry and get_slot_availability(placement, entry.starts_at, entry.ends_at)["remaining"]:
        try:
            _send_waitlist_offer(entry)
        except Exception:
            import logging
            logging.getLogger(__name__).exception("Could not email advertising waitlist entry %s", entry.pk)


class AdCampaignViewSet(viewsets.ModelViewSet):
    platform_feature = "ads_enabled"
    serializer_class = AdCampaignSerializer
    permission_classes = (IsVerifiedStudent, PlatformFeatureEnabled)

    def get_throttles(self):
        if getattr(self, "action", None) == "public_submit":
            return [PublicAdSubmissionThrottle()]
        if getattr(self, "action", None) in ("public_manage", "public_cancel"):
            return [PublicAdManageThrottle()]
        return super().get_throttles()

    def get_permissions(self):
        if getattr(self, "action", None) in ("list", "retrieve", "image_file", "click"):
            return [permissions.AllowAny(), PlatformFeatureEnabled()]
        if getattr(self, "action", None) in ("public_submit", "public_manage", "public_cancel"):
            return [permissions.AllowAny(), PlatformFeatureEnabled()]
        if getattr(self, "action", None) in ("approve", "reject", "pause", "activate"):
            return [permissions.IsAdminUser()]
        if self.request.user and self.request.user.is_staff:
            return [permissions.IsAdminUser()]
        return [IsVerifiedStudent(), PlatformFeatureEnabled()]

    def get_queryset(self):
        if self.request.user.is_staff:
            return AdCampaign.objects.select_related("advertiser", "business_advertiser", "ad_placement", "package").all()
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
            "jobs": (platform.jobs_enabled, "jobs"),
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
            placement_key = self.request.query_params.get("placement")
            if placement_key:
                active = active.filter(Q(ad_placement__key=placement_key, ad_placement__is_active=True) | Q(ad_placements__key=placement_key, ad_placements__is_active=True)).distinct()
            else:
                page_text = Cast("pages", output_field=CharField())
                active = active.filter(Q(IContains(page_text, Value(f'"{page_value}"'))))
            return active
        if self.request.user.is_authenticated:
            return active | AdCampaign.objects.filter(advertiser=self.request.user)
        return active

    @action(detail=False, methods=["post"], url_path="public-submit")
    @transaction.atomic
    def public_submit(self, request):
        platform = PlatformSettings.get_solo()
        if not platform.ads_enabled:
            raise PermissionDenied("Advertising submissions are currently closed.")
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if not serializer.validated_data.get("image"):
            raise ValidationError({"image": "Upload an advertisement image before submitting."})
        placements = serializer.validated_data.get("ad_placements") or []
        placement = serializer.validated_data.get("ad_placement")
        if not placements and placement:
            placements = [placement]
        if not placement or not placements or any(not item.is_active for item in placements):
            raise ValidationError({"ad_placement": "Choose an active advertising placement."})
        plan = serializer.validated_data.get("package")
        if not plan or not plan.is_active:
            raise ValidationError({"package": "Choose an active advertising package."})
        allowed_placements = plan.allowed_placements
        if not allowed_placements:
            allowed_placements = [item.key for item in placements if item.page in plan.allowed_pages]
        if len(placements) > plan.max_pages or any(item.key not in allowed_placements for item in placements):
            raise ValidationError({"ad_placement": "This package is not available for the selected placement."})
        start_date = parse_date(str(request.data.get("start_date", ""))) or timezone.localdate()
        if start_date < timezone.localdate():
            raise ValidationError({"start_date": "Choose today or a future date."})
        end_date = start_date + timedelta(days=plan.duration_days - 1)
        (starts_at, ends_at) = local_date_window(start_date, end_date)
        placement_ids = sorted(item.pk for item in placements)
        locked_placements = list(AdPlacement.objects.select_for_update().filter(pk__in=placement_ids).order_by("pk"))
        if len(locked_placements) != len(placement_ids):
            raise ValidationError({"ad_placement": "One or more selected placements are unavailable."})
        placements = locked_placements
        placement = next((item for item in placements if item.pk == placement.pk), placements[0])
        waitlist_token = str(request.data.get("waitlist_token", "")).strip()
        waitlist_entry = None
        if waitlist_token:
            waitlist_entry = AdWaitlistEntry.objects.select_for_update().filter(
                offer_token_hash=_token_hash(waitlist_token),
                status=AdWaitlistEntry.Status.OFFERED,
                offer_expires_at__gt=timezone.now(),
                placement__in=placements,
                starts_at=starts_at,
                ends_at=ends_at,
            ).select_related("advertiser").first()
            if not waitlist_entry:
                raise ValidationError({"waitlist_token": "This advertising slot offer has expired or is no longer valid."})
        full_placements = [item for item in placements if get_slot_availability(item, starts_at, ends_at)["remaining"] <= 0]
        if full_placements:
            raise ValidationError({"availability": "This placement has just become fully booked. Join the waitlist to be notified when it opens."})

        business_name = str(request.data.get("business_name", "")).strip()[:160]
        contact_name = str(request.data.get("contact_name", "")).strip()[:120]
        email = str(request.data.get("contact_email", request.data.get("email", ""))).strip().lower()
        phone = str(request.data.get("phone", "")).strip()[:32]
        if len(business_name) < 2 or len(contact_name) < 2 or len(phone) < 7 or "@" not in email:
            raise ValidationError({"advertiser": "Provide a business name, contact name, valid email and phone number."})
        advertiser = AdAdvertiser.objects.filter(email__iexact=email).first()
        if advertiser is None:
            advertiser = AdAdvertiser.objects.create(
                business_name=business_name,
                contact_name=contact_name,
                email=email,
                phone=phone,
                location=str(request.data.get("location", "")).strip()[:160],
                website=str(request.data.get("website", "")).strip()[:500],
            )
        else:
            advertiser.business_name = business_name
            advertiser.contact_name = contact_name
            advertiser.phone = phone
            advertiser.location = str(request.data.get("location", "")).strip()[:160]
            advertiser.website = str(request.data.get("website", "")).strip()[:500]
            advertiser.save(update_fields=("business_name", "contact_name", "phone", "location", "website", "updated_at"))

        token = secrets.token_urlsafe(32)
        advertiser.management_token_hash = _token_hash(token)
        advertiser.save(update_fields=("management_token_hash", "updated_at"))
        campaign = serializer.save(
            advertiser=None,
            business_advertiser=advertiser,
            placement=placement.page,
            ad_placement=placement,
            ad_placements=placements,
            pages=sorted({item.page for item in placements}),
            business_name=business_name,
            status=AdCampaign.Status.PENDING_REVIEW,
            starts_at=starts_at,
            ends_at=ends_at,
            duration_days=plan.duration_days,
            price_kobo=plan.price_kobo if platform.ad_fee_enabled and platform.paystack_enabled else 0,
        )
        if waitlist_entry:
            waitlist_entry.status = AdWaitlistEntry.Status.BOOKED
            waitlist_entry.offer_token_hash = ""
            waitlist_entry.save(update_fields=("status", "offer_token_hash", "updated_at"))

        manage_url = f"{os.getenv('FRONTEND_ORIGIN', 'http://localhost:3000').split(',')[0].rstrip('/')}/advertise/manage?token={token}"
        from django.db import transaction as db_transaction
        from apps.notifications.email import send_tarmac_email
        db_transaction.on_commit(lambda: send_tarmac_email(
            subject="Tarmac received your advertising request",
            text_body=f"We received your campaign request for {placement.position}. It is pending review. We will email you when the review is complete. You can securely check its status here.",
            recipient=advertiser.email,
            recipient_name=advertiser.contact_name,
            action_url=manage_url,
            action_label="View campaign status",
        ))
        return Response({"id": campaign.pk, "status": campaign.status, "placement": placement.position, "start_date": start_date, "end_date": end_date, "detail": "Your campaign is awaiting Tarmac review. We sent a private campaign link to your email."}, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"], url_path="public-manage")
    def public_manage(self, request):
        token = str(request.query_params.get("token", "")).strip()
        if not token:
            raise ValidationError({"token": "A campaign access link is required."})
        advertiser = get_object_or_404(AdAdvertiser, management_token_hash=_token_hash(token))
        rows = []
        for campaign in advertiser.campaigns.select_related("ad_placement", "package").prefetch_related("payments").order_by("-created_at"):
            payment = campaign.payments.order_by("-created_at").first()
            rows.append({
                "id": campaign.pk,
                "business_name": campaign.business_name,
                "title": campaign.title,
                "status": campaign.status,
                "placement": ", ".join(campaign.ad_placements.values_list("position", flat=True)) or (campaign.ad_placement.position if campaign.ad_placement_id else campaign.placement),
                "placement_key": campaign.ad_placement.key if campaign.ad_placement_id else "",
                "start_date": timezone.localtime(campaign.starts_at).date() if campaign.starts_at else None,
                "end_date": (timezone.localtime(campaign.ends_at) - timedelta(seconds=1)).date() if campaign.ends_at else None,
                "amount_kobo": campaign.price_kobo,
                "payment_status": payment.status if payment else None,
                "authorization_url": (payment.provider_response.get("data", {}).get("authorization_url") if payment and payment.status == "initialized" else None),
            })
        return Response({"business_name": advertiser.business_name, "campaigns": rows})

    @action(detail=False, methods=["post"], url_path="public-cancel")
    @transaction.atomic
    def public_cancel(self, request):
        token = str(request.data.get("token", "")).strip()
        campaign_id = request.data.get("campaign")
        advertiser = get_object_or_404(AdAdvertiser, management_token_hash=_token_hash(token))
        campaign = get_object_or_404(AdCampaign.objects.select_for_update().select_related("ad_placement"), pk=campaign_id, business_advertiser=advertiser)
        if campaign.status in (AdCampaign.Status.ENDED, AdCampaign.Status.CANCELLED, AdCampaign.Status.REJECTED):
            raise ValidationError({"status": "This campaign is already closed."})
        campaign.status = AdCampaign.Status.CANCELLED
        campaign.save(update_fields=("status", "updated_at"))
        AdSlotHold.objects.filter(campaign=campaign).delete()
        placements = list(campaign.ad_placements.all()) or ([campaign.ad_placement] if campaign.ad_placement_id else [])
        if campaign.starts_at and campaign.ends_at:
            for placement in placements:
                transaction.on_commit(lambda slot=placement: _offer_next_waitlisted(slot, campaign.starts_at, campaign.ends_at))
        return Response({"status": campaign.status})

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
            campaign_placements = list(campaign.ad_placements.all()) or _default_placements_for_pages(campaign.pages)
            if campaign_placements:
                campaign.ad_placements.set(campaign_placements)
                if not campaign.ad_placement_id:
                    campaign.ad_placement = campaign_placements[0]
                    campaign.save(update_fields=("ad_placement", "updated_at"))
            AdminLog.objects.create(actor=self.request.user, action="admin_ad_campaign_created", target_type="AdCampaign", target_id=str(campaign.pk), metadata={"advertiser_id": campaign.advertiser_id, "price_kobo": campaign.price_kobo})
            return
        package = serializer.validated_data.get("package")
        if package is None:
            raise ValidationError({"package": "Choose an active advertising package."})
        needs_payment = platform.ad_fee_enabled and platform.paystack_enabled and package.price_kobo > 0
        initial_status = AdCampaign.Status.PENDING_PAYMENT if needs_payment else AdCampaign.Status.PENDING_REVIEW
        campaign = serializer.save(advertiser=self.request.user, status=initial_status, price_kobo=package.price_kobo, duration_days=package.duration_days)
        campaign_placements = list(campaign.ad_placements.all()) or _default_placements_for_pages(campaign.pages)
        if campaign_placements:
            campaign.ad_placements.set(campaign_placements)
            if not campaign.ad_placement_id:
                campaign.ad_placement = campaign_placements[0]
                campaign.save(update_fields=("ad_placement", "updated_at"))

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
    @transaction.atomic
    def approve(self, request, pk=None):
        campaign = get_object_or_404(AdCampaign.objects.select_for_update().select_related("business_advertiser", "ad_placement", "package", "advertiser"), pk=pk)
        platform = PlatformSettings.get_solo()
        from apps.payments.models import Payment
        payment_required = platform.ad_fee_enabled and platform.paystack_enabled and campaign.price_kobo > 0
        paid = Payment.objects.filter(advertisement=campaign, status=Payment.Status.SUCCESS, amount_kobo=campaign.price_kobo).exists()
        if not payment_required and not paid and campaign.price_kobo:
            campaign.price_kobo = 0
            campaign.save(update_fields=("price_kobo", "updated_at"))
        if campaign.status != AdCampaign.Status.PENDING_REVIEW:
            raise ValidationError({"status": "Only campaigns awaiting review can be approved."})
        if payment_required and not campaign.business_advertiser_id and not paid:
            raise ValidationError({"payment": "A successful payment is required before approval."})
        if campaign.advertiser_id and not is_student_feature_eligible(campaign.advertiser):
            raise ValidationError({"advertiser": "The campaign owner must have an active student account and verified email."})
        override = request.data.get("override_capacity") is True
        override_reason = str(request.data.get("override_reason", "")).strip()
        now = timezone.now()
        booking_starts_at = campaign.starts_at or now
        booking_ends_at = campaign.ends_at or booking_starts_at + timedelta(days=campaign.duration_days)
        placements = list(campaign.ad_placements.order_by("pk")) or ([campaign.ad_placement] if campaign.ad_placement_id else [])
        locked_placements = list(AdPlacement.objects.select_for_update().filter(pk__in=[item.pk for item in placements]).order_by("pk"))
        full_placements = [item for item in locked_placements if get_slot_availability(item, booking_starts_at, booking_ends_at, exclude_campaign_id=campaign.pk)["remaining"] <= 0]
        if full_placements:
            if not override or not request.user.is_superuser or len(override_reason) < 5:
                raise ValidationError({"availability": f"Fully booked for {', '.join(item.position for item in full_placements)}. A superuser must provide a reason to override capacity."})
        campaign.approved_at = now
        campaign.starts_at = booking_starts_at
        campaign.ends_at = booking_ends_at
        if campaign.business_advertiser_id and payment_required and not paid:
            campaign.status = AdCampaign.Status.PENDING_PAYMENT
            for placement in locked_placements:
                AdSlotHold.objects.update_or_create(
                    campaign=campaign, placement=placement,
                    defaults={"starts_at": booking_starts_at, "ends_at": booking_ends_at, "expires_at": now + timedelta(minutes=30), "is_confirmed": False},
                )
        else:
            campaign.status = AdCampaign.Status.SCHEDULED if campaign.starts_at > now else AdCampaign.Status.ACTIVE
            if campaign.business_advertiser_id and paid:
                for placement in locked_placements:
                    AdSlotHold.objects.update_or_create(campaign=campaign, placement=placement, defaults={"starts_at": booking_starts_at, "ends_at": booking_ends_at, "expires_at": now, "is_confirmed": True})
        campaign.save(update_fields=("status", "approved_at", "starts_at", "ends_at", "updated_at"))
        if campaign.package_id and campaign.advertiser_id:
            successful_payment = Payment.objects.filter(advertisement=campaign, status=Payment.Status.SUCCESS).order_by("-paid_at").first()
            from apps.administration.models import StudentSubscription
            StudentSubscription.objects.get_or_create(ad_campaign=campaign, defaults={"user": campaign.advertiser, "plan": campaign.package, "amount_kobo": successful_payment.amount_kobo if successful_payment else 0, "payment_reference": successful_payment.reference if successful_payment else None, "starts_at": campaign.starts_at, "ends_at": campaign.ends_at})
        from apps.administration.models import AdminLog
        AdminLog.objects.create(actor=request.user, action="ad_campaign_approved", target_type="AdCampaign", target_id=str(campaign.pk), metadata={"capacity_override": override, "override_reason": override_reason if override else ""})
        if campaign.business_advertiser_id:
            from apps.notifications.email import send_tarmac_email
            advertiser = campaign.business_advertiser
            token = secrets.token_urlsafe(32)
            advertiser.management_token_hash = _token_hash(token)
            advertiser.save(update_fields=("management_token_hash", "updated_at"))
            manage_url = f"{os.getenv('FRONTEND_ORIGIN', 'http://localhost:3000').split(',')[0].rstrip('/')}/advertise/manage?token={token}"
            action_url = manage_url
            body = f"Your campaign ‘{campaign.title}’ was approved. Choose Complete payment in your secure campaign dashboard. Your date range is {timezone.localtime(campaign.starts_at).date()} to {(timezone.localtime(campaign.ends_at) - timedelta(seconds=1)).date()}."
            if not payment_required or paid:
                body = f"Your campaign ‘{campaign.title}’ was approved and is {campaign.status}. It will display during your scheduled dates."
            transaction.on_commit(lambda: send_tarmac_email(subject="Your Tarmac advertisement was approved", text_body=body, recipient=advertiser.email, recipient_name=advertiser.contact_name, action_url=action_url, action_label="Open campaign dashboard"))
        else:
            from apps.notifications.services import notify_user
            notify_user(campaign.advertiser, "Ad campaign approved", f"Your campaign '{campaign.title}' is now {campaign.status}.", {"kind": "ad_approved"}, f"ad-approved:{campaign.pk}")
        return Response({"status": campaign.status})

    @action(detail=True, methods=("post",), permission_classes=(permissions.IsAdminUser,))
    def reject(self, request, pk=None):
        campaign = self.get_object()
        if campaign.status == AdCampaign.Status.ACTIVE:
            raise ValidationError({"status": "Pause an active campaign before rejecting it."})
        campaign.status = AdCampaign.Status.REJECTED
        campaign.save(update_fields=("status", "updated_at"))
        AdSlotHold.objects.filter(campaign=campaign).delete()
        placements = list(campaign.ad_placements.all()) or ([campaign.ad_placement] if campaign.ad_placement_id else [])
        if campaign.starts_at and campaign.ends_at:
            for placement in placements:
                transaction.on_commit(lambda slot=placement: _offer_next_waitlisted(slot, campaign.starts_at, campaign.ends_at))
        if campaign.business_advertiser_id:
            from apps.notifications.email import send_tarmac_email
            transaction.on_commit(lambda: send_tarmac_email(subject="Update on your Tarmac advertisement", text_body=f"Your campaign ‘{campaign.title}’ was not approved. Contact Tarmac support if you need more information.", recipient=campaign.business_advertiser.email, recipient_name=campaign.business_advertiser.contact_name, action_url=f"{os.getenv('FRONTEND_ORIGIN', 'http://localhost:3000').split(',')[0].rstrip('/')}/support", action_label="Contact support"))
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

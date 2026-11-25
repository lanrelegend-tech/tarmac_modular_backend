import hashlib
import hmac
import secrets
import os
import uuid
from datetime import datetime, timedelta

from django.conf import settings
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
from rest_framework.throttling import UserRateThrottle

from apps.accounts.permissions import IsVerifiedStudent, is_student_feature_eligible
from config.storage import optimized_image_response
from apps.administration.models import PlatformSettings
from apps.administration.permissions import IsAdminOrAbove, PlatformFeatureEnabled
from .models import AdAdvertiser, AdBookmark, AdCampaign, AdImpression, AdPlacement, AdSlotHold, AdWaitlistEntry
from .serializers import AdCampaignSerializer, AdPlacementSerializer, AdWaitlistEntrySerializer
from .services import get_slot_availability, local_date_window
from .throttles import PublicAdImpressionThrottle, PublicAdManageThrottle, PublicAdSubmissionThrottle


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
    # Placement setup is managed by the dedicated admin-tools endpoint. Keep
    # this endpoint read-only for placement records; public waitlist signup is
    # the only POST action it needs to accept.
    http_method_names = ["get", "post", "head", "options"]

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
        return [IsAdminOrAbove()]

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
        earlier = AdWaitlistEntry.objects.filter(placement=placement, starts_at=starts_at, ends_at=ends_at, status__in=(AdWaitlistEntry.Status.WAITING, AdWaitlistEntry.Status.OFFERED), created_at__lt=entry.created_at).count()
        return Response({"id": entry.pk, "status": entry.status, "queue_position": earlier + 1 if entry.status == AdWaitlistEntry.Status.WAITING else None, "created": created, "placement": placement.position, "start_date": start_date, "end_date": end_date}, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class AdminAdWaitlistViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = AdWaitlistEntrySerializer
    permission_classes = [IsAdminOrAbove]

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
        another_offer = AdWaitlistEntry.objects.filter(placement=entry.placement, status=AdWaitlistEntry.Status.OFFERED, offer_expires_at__gt=timezone.now(), starts_at__lt=entry.ends_at, ends_at__gt=entry.starts_at).exclude(pk=entry.pk).exists()
        if another_offer:
            raise ValidationError({"status": "Another advertiser already has an active 24-hour offer for overlapping dates."})
        if get_slot_availability(entry.placement, entry.starts_at, entry.ends_at)["remaining"] <= 0:
            raise ValidationError({"availability": "This placement is still fully booked for those dates."})
        _send_waitlist_offer(entry, request.user)
        return Response({"status": entry.status, "offer_expires_at": entry.offer_expires_at})

    @action(detail=True, methods=["post"], url_path="cancel")
    @transaction.atomic
    def cancel(self, request, pk=None):
        entry = get_object_or_404(AdWaitlistEntry.objects.select_for_update().select_related("placement"), pk=pk)
        if entry.status not in (AdWaitlistEntry.Status.WAITING, AdWaitlistEntry.Status.OFFERED):
            raise ValidationError({"status": "Only waiting or offered entries can be cancelled."})
        entry.status = AdWaitlistEntry.Status.CANCELLED
        entry.offer_token_hash = ""
        entry.offer_expires_at = None
        entry.save(update_fields=("status", "offer_token_hash", "offer_expires_at", "updated_at"))
        from apps.administration.models import AdminLog
        AdminLog.objects.create(actor=request.user, action="ad_waitlist_entry_cancelled", target_type="AdWaitlistEntry", target_id=str(entry.pk), metadata={"placement": entry.placement.key})
        transaction.on_commit(lambda: _offer_next_waitlisted(entry.placement, entry.starts_at, entry.ends_at))
        return Response({"status": entry.status})


class AdminAdPlacementViewSet(viewsets.ModelViewSet):
    serializer_class = AdPlacementSerializer
    queryset = AdPlacement.objects.exclude(key__endswith=".featured_brand")
    permission_classes = [IsAdminOrAbove]
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
    now = timezone.now()
    expired_campaign_ids = list(AdSlotHold.objects.filter(
        placement=placement,
        is_confirmed=False,
        expires_at__lte=now,
        campaign__status=AdCampaign.Status.PENDING_PAYMENT,
    ).values_list("campaign_id", flat=True).distinct())
    if expired_campaign_ids:
        unpaid_ids = list(AdCampaign.objects.filter(pk__in=expired_campaign_ids).exclude(payments__status="success").values_list("pk", flat=True).distinct())
        AdSlotHold.objects.filter(campaign_id__in=unpaid_ids).delete()
        AdWaitlistEntry.objects.filter(campaigns__pk__in=unpaid_ids).update(status=AdWaitlistEntry.Status.WAITING, offer_token_hash="", offer_expires_at=None, created_at=now)
    AdWaitlistEntry.objects.filter(
        placement=placement,
        status=AdWaitlistEntry.Status.OFFERED,
        offer_expires_at__lte=now,
        starts_at__lt=ends_at,
        ends_at__gt=starts_at,
    ).update(status=AdWaitlistEntry.Status.WAITING, offer_token_hash="", offer_expires_at=None, created_at=now)
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
        if getattr(self, "action", None) == "impression":
            return [PublicAdImpressionThrottle(), UserRateThrottle()]
        return super().get_throttles()

    def get_permissions(self):
        if getattr(self, "action", None) in ("list", "retrieve", "image_file", "click"):
            return [permissions.AllowAny(), PlatformFeatureEnabled()]
        if getattr(self, "action", None) == "impression" and not self.request.user.is_authenticated:
            return [permissions.AllowAny(), PlatformFeatureEnabled()]
        if getattr(self, "action", None) in ("public_submit", "public_manage", "public_cancel"):
            return [permissions.AllowAny(), PlatformFeatureEnabled()]
        if getattr(self, "action", None) in ("approve", "reject", "pause", "activate"):
            return [IsAdminOrAbove()]
        if self.request.user and self.request.user.is_staff:
            return [IsAdminOrAbove()]
        return [IsVerifiedStudent(), PlatformFeatureEnabled()]

    def get_queryset(self):
        saved_only = self.request.query_params.get("saved") == "true"
        if self.request.user.is_staff and not saved_only:
            return AdCampaign.objects.select_related("advertiser", "business_advertiser", "ad_placement", "package").prefetch_related("payments", "ad_placements").all()
        now = timezone.now()
        active = AdCampaign.objects.filter(status=AdCampaign.Status.ACTIVE).prefetch_related("payments", "ad_placements").filter(
            models_q_start(now), models_q_end(now)
        )
        if saved_only:
            if not self.request.user.is_authenticated:
                return AdCampaign.objects.none()
            active = active.filter(bookmarks__user=self.request.user).distinct()
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
                # Marketplace now has one visible carousel at the top of its
                # page. Keep campaigns booked in the former lower card slot
                # visible in that same carousel so paid ads are not lost.
                display_keys = [placement_key]
                if placement_key == "marketplace.banner":
                    display_keys.append("marketplace.sponsored_card")
                placement_filter = Q()
                for key in display_keys:
                    placement_filter |= Q(ad_placement__key=key, ad_placement__is_active=True)
                    placement_filter |= Q(ad_placements__key=key, ad_placements__is_active=True)
                active = active.filter(placement_filter).distinct()
            else:
                page_text = Cast("pages", output_field=CharField())
                active = active.filter(Q(IContains(page_text, Value(f'"{page_value}"'))))
            return active
        if saved_only:
            return active
        if self.request.user.is_authenticated:
            return active | AdCampaign.objects.filter(advertiser=self.request.user).prefetch_related("payments", "ad_placements")
        return active

    @action(detail=False, methods=["post"], url_path="public-submit")
    @transaction.atomic
    def public_submit(self, request):
        platform = PlatformSettings.get_solo()
        if not platform.ads_enabled:
            raise PermissionDenied("Advertising submissions are currently closed.")
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        accepted_policy = str(request.data.get("advertising_policy_accepted", "")).lower() in ("true", "1", "yes")
        if not accepted_policy:
            raise ValidationError({"advertising_policy_accepted": "Confirm that your advert follows Tarmac's advertising rules before submitting."})
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
        if not advertiser.management_token_hash:
            advertiser.management_token_hash = _token_hash(token)
            advertiser.save(update_fields=("management_token_hash", "updated_at"))
        needs_payment = platform.ad_fee_enabled and platform.paystack_enabled and plan.price_kobo > 0
        campaign = serializer.save(
            advertiser=None,
            business_advertiser=advertiser,
            management_token_hash=_token_hash(token),
            waitlist_entry=waitlist_entry,
            placement=placement.page,
            ad_placement=placement,
            ad_placements=placements,
            pages=sorted({item.page for item in placements}),
            business_name=business_name,
            status=AdCampaign.Status.PENDING_PAYMENT if needs_payment else AdCampaign.Status.PENDING_REVIEW,
            starts_at=starts_at,
            ends_at=ends_at,
            duration_days=plan.duration_days,
            price_kobo=plan.price_kobo if platform.ad_fee_enabled and platform.paystack_enabled else 0,
            plan_name_snapshot=plan.name,
            plan_price_kobo=plan.price_kobo,
            payment_required_snapshot=needs_payment,
            advertising_policy_accepted=True,
            advertising_policy_accepted_at=timezone.now(),
        )
        if campaign.status == AdCampaign.Status.PENDING_REVIEW:
            from apps.notifications.staff import notify_staff_section
            notify_staff_section("ads", "Advertisement needs review", f'"{campaign.title}" was submitted and is waiting for approval.', {"kind": "ad_review", "campaign_id": campaign.pk}, f"ad-review:{campaign.pk}")
        for slot in placements:
            AdSlotHold.objects.update_or_create(campaign=campaign, placement=slot, defaults={"starts_at": starts_at, "ends_at": ends_at, "expires_at": timezone.now() + timedelta(minutes=30) if needs_payment else ends_at, "is_confirmed": not needs_payment})
        if waitlist_entry:
            waitlist_entry.status = AdWaitlistEntry.Status.BOOKED
            waitlist_entry.offer_token_hash = ""
            waitlist_entry.save(update_fields=("status", "offer_token_hash", "updated_at"))

        manage_url = f"{os.getenv('FRONTEND_ORIGIN', 'http://localhost:3000').split(',')[0].rstrip('/')}/advertise/manage?token={token}"
        from django.db import transaction as db_transaction
        from apps.notifications.email import send_tarmac_email
        db_transaction.on_commit(lambda: send_tarmac_email(
            subject="Tarmac received your advertising request",
            text_body=(f"We received your campaign request for {placement.position}. Complete payment from your private campaign page. After Paystack confirms payment, Tarmac will review the ad before it can go live." if needs_payment else f"We received your campaign request for {placement.position}. No payment is due. Tarmac will review the ad before it can go live."),
            recipient=advertiser.email,
            recipient_name=advertiser.contact_name,
            action_url=manage_url,
            action_label="View campaign and payment status",
        ))
        return Response({"id": campaign.pk, "status": campaign.status, "placement": placement.position, "start_date": start_date, "end_date": end_date, "management_url": manage_url, "detail": "We sent a private campaign link to your email. Complete payment before Tarmac reviews the ad." if needs_payment else "We sent a private campaign link to your email. Tarmac will review the ad before it can go live."}, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"], url_path="public-manage")
    def public_manage(self, request):
        token = str(request.query_params.get("token", "")).strip()
        if not token:
            raise ValidationError({"token": "A campaign access link is required."})
        token_hash = _token_hash(token)
        campaign_access = AdCampaign.objects.filter(management_token_hash=token_hash, business_advertiser__isnull=False).select_related("business_advertiser").first()
        if campaign_access:
            advertiser = campaign_access.business_advertiser
            campaigns = AdCampaign.objects.filter(pk=campaign_access.pk)
        else:
            advertiser = get_object_or_404(AdAdvertiser, management_token_hash=token_hash)
            campaigns = advertiser.campaigns.all()
        rows = []
        for campaign in campaigns.select_related("ad_placement", "package").prefetch_related("payments", "ad_placements").order_by("-created_at"):
            payment_rows = list(campaign.payments.order_by("-created_at"))
            payment = payment_rows[0] if payment_rows else None
            paid_rows = [item for item in payment_rows if item.status == "success"]
            rows.append({
                "id": campaign.pk,
                "business_name": campaign.business_name,
                "title": campaign.title,
                "plan_name": campaign.plan_name_snapshot or (campaign.package.name if campaign.package_id else ""),
                "plan_price_kobo": campaign.plan_price_kobo,
                "duration_days": campaign.duration_days,
                "target_url": campaign.target_url,
                "status": campaign.status,
                "placement": ", ".join(campaign.ad_placements.values_list("position", flat=True)) or (campaign.ad_placement.position if campaign.ad_placement_id else campaign.placement),
                "placement_key": campaign.ad_placement.key if campaign.ad_placement_id else "",
                "start_date": timezone.localtime(campaign.starts_at).date() if campaign.starts_at else None,
                "end_date": (timezone.localtime(campaign.ends_at) - timedelta(seconds=1)).date() if campaign.ends_at else None,
                "amount_kobo": campaign.price_kobo,
                "payment_required": campaign.payment_required_snapshot,
                "impressions": campaign.impression_count,
                "clicks": campaign.click_count,
                "payment_status": payment.status if payment else None,
                "refund_status": payment.refund_status if payment else "",
                "payment_reference": payment.reference if payment else "",
                "paid_amount_kobo": sum(item.amount_kobo for item in paid_rows),
                "payment_history": [{"reference": item.reference, "amount_kobo": item.amount_kobo, "currency": item.currency, "status": item.status, "refund_status": item.refund_status, "created_at": item.created_at, "paid_at": item.paid_at} for item in payment_rows],
                "reviewer_note": campaign.reviewer_note,
                "paid": bool(paid_rows),
                "authorization_url": (payment.provider_response.get("data", {}).get("authorization_url") if payment and payment.status == "initialized" else None),
            })
        return Response({"business_name": advertiser.business_name, "campaigns": rows})

    @action(detail=False, methods=["post"], url_path="public-cancel")
    @transaction.atomic
    def public_cancel(self, request):
        token = str(request.data.get("token", "")).strip()
        campaign_id = request.data.get("campaign")
        token_hash = _token_hash(token)
        campaign = AdCampaign.objects.select_for_update(of=("self",)).select_related("ad_placement", "business_advertiser").filter(pk=campaign_id, management_token_hash=token_hash).first()
        if campaign is None:
            advertiser = get_object_or_404(AdAdvertiser, management_token_hash=token_hash)
            campaign = get_object_or_404(AdCampaign.objects.select_for_update(of=("self",)).select_related("ad_placement"), pk=campaign_id, business_advertiser=advertiser)
        if campaign.status in (AdCampaign.Status.ENDED, AdCampaign.Status.CANCELLED, AdCampaign.Status.REJECTED):
            raise ValidationError({"status": "This campaign is already closed."})
        if campaign.payments.filter(status="success").exists():
            raise ValidationError({"status": "This campaign has been paid. Contact Tarmac Support to request cancellation and review any refund."})
        if campaign.payments.filter(status="initialized").exists():
            raise ValidationError({"status": "A Paystack checkout is still open for this campaign. Wait for Paystack to confirm its result before cancelling."})
        previous_status = campaign.status
        campaign.status = AdCampaign.Status.CANCELLED
        campaign.save(update_fields=("status", "updated_at"))
        from apps.administration.models import AdminLog
        AdminLog.objects.create(
            actor=campaign.advertiser,
            action="advertiser_ad_campaign_cancelled",
            target_type="AdCampaign",
            target_id=str(campaign.pk),
            metadata={"previous_status": previous_status, "business_advertiser_id": campaign.business_advertiser_id},
        )
        AdSlotHold.objects.filter(campaign=campaign).delete()
        placements = list(campaign.ad_placements.all()) or ([campaign.ad_placement] if campaign.ad_placement_id else [])
        if campaign.starts_at and campaign.ends_at:
            for placement in placements:
                transaction.on_commit(lambda slot=placement: _offer_next_waitlisted(slot, campaign.starts_at, campaign.ends_at))
        return Response({"status": campaign.status})

    @transaction.atomic
    def perform_create(self, serializer):
        platform = PlatformSettings.get_solo()
        if self.request.user.is_staff:
            from apps.administration.models import AdminLog
            from django.utils import timezone as django_timezone
            package = serializer.validated_data.get("package")
            if package is None:
                raise ValidationError({"package": "Choose a plan so this complimentary campaign has clear duration and placement terms."})
            start = serializer.validated_data.get("starts_at") or django_timezone.now()
            duration_days = package.duration_days
            end = serializer.validated_data.get("ends_at") or start + timedelta(days=duration_days)
            selected_placements = serializer.validated_data.get("ad_placements") or []
            if not selected_placements:
                selected_placements = [serializer.validated_data.get("ad_placement")] if serializer.validated_data.get("ad_placement") else _default_placements_for_pages(serializer.validated_data.get("pages", []))
            if not selected_placements:
                raise ValidationError({"ad_placements": "Choose at least one active placement."})
            locked_placements = list(AdPlacement.objects.select_for_update().filter(pk__in=sorted(item.pk for item in selected_placements), is_active=True).order_by("pk"))
            if len(locked_placements) != len({item.pk for item in selected_placements}):
                raise ValidationError({"ad_placements": "One or more selected placements are unavailable."})
            full = [slot for slot in locked_placements if get_slot_availability(slot, start, end)["remaining"] <= 0]
            if full:
                raise ValidationError({"availability": f"These placements are fully booked for the selected dates: {', '.join(slot.position for slot in full)}."})
            campaign = serializer.save(
                status=AdCampaign.Status.SCHEDULED if start > django_timezone.now() else AdCampaign.Status.ACTIVE,
                starts_at=start,
                ends_at=end,
                duration_days=duration_days,
                price_kobo=0,
                plan_name_snapshot=package.name,
                plan_price_kobo=package.price_kobo,
                payment_required_snapshot=False,
            )
            campaign_placements = locked_placements
            if campaign_placements:
                campaign.ad_placements.set(campaign_placements)
                if not campaign.ad_placement_id:
                    campaign.ad_placement = campaign_placements[0]
                    campaign.save(update_fields=("ad_placement", "updated_at"))
                for slot in campaign_placements:
                    AdSlotHold.objects.update_or_create(campaign=campaign, placement=slot, defaults={"starts_at": start, "ends_at": end, "expires_at": end, "is_confirmed": True})
            AdminLog.objects.create(actor=self.request.user, action="admin_ad_campaign_created", target_type="AdCampaign", target_id=str(campaign.pk), metadata={"advertiser_id": campaign.advertiser_id, "price_kobo": campaign.price_kobo})
            return
        package = serializer.validated_data.get("package")
        if package is None:
            raise ValidationError({"package": "Choose an active advertising package."})
        now = timezone.now()
        starts_at = serializer.validated_data.get("starts_at") or now
        ends_at = serializer.validated_data.get("ends_at") or starts_at + timedelta(days=package.duration_days)
        placements = serializer.validated_data.get("ad_placements") or []
        if not placements:
            placements = [serializer.validated_data.get("ad_placement")] if serializer.validated_data.get("ad_placement") else _default_placements_for_pages(serializer.validated_data.get("pages", []))
        if not placements:
            raise ValidationError({"ad_placements": "Choose at least one active advertising placement."})
        placements = list(AdPlacement.objects.select_for_update().filter(pk__in=sorted(item.pk for item in placements), is_active=True).order_by("pk"))
        if not placements:
            raise ValidationError({"ad_placements": "The selected advertising placements are not available."})
        full = [slot for slot in placements if get_slot_availability(slot, starts_at, ends_at)["remaining"] <= 0]
        if full:
            raise ValidationError({"availability": f"These placements are fully booked for the selected dates: {', '.join(slot.position for slot in full)}."})
        needs_payment = platform.ad_fee_enabled and platform.paystack_enabled and package.price_kobo > 0
        initial_status = AdCampaign.Status.PENDING_PAYMENT if needs_payment else AdCampaign.Status.PENDING_REVIEW
        campaign = serializer.save(
            advertiser=self.request.user,
            status=initial_status,
            starts_at=starts_at,
            ends_at=ends_at,
            price_kobo=package.price_kobo if needs_payment else 0,
            duration_days=package.duration_days,
            plan_name_snapshot=package.name,
            plan_price_kobo=package.price_kobo,
            payment_required_snapshot=needs_payment,
        )
        if campaign.status == AdCampaign.Status.PENDING_REVIEW:
            from apps.notifications.staff import notify_staff_section
            notify_staff_section("ads", "Advertisement needs review", f'"{campaign.title}" was submitted and is waiting for approval.', {"kind": "ad_review", "campaign_id": campaign.pk}, f"ad-review:{campaign.pk}")
        campaign_placements = list(campaign.ad_placements.all()) or _default_placements_for_pages(campaign.pages)
        if campaign_placements:
            campaign.ad_placements.set(campaign_placements)
            if not campaign.ad_placement_id:
                campaign.ad_placement = campaign_placements[0]
                campaign.save(update_fields=("ad_placement", "updated_at"))
        for slot in campaign_placements:
            AdSlotHold.objects.update_or_create(campaign=campaign, placement=slot, defaults={"starts_at": starts_at, "ends_at": ends_at, "expires_at": now + timedelta(minutes=30) if needs_payment else ends_at, "is_confirmed": not needs_payment})

    def perform_update(self, serializer):
        if not self.request.user.is_staff and serializer.instance.advertiser_id != self.request.user.id:
            raise PermissionDenied("You can only update your own ad campaign.")
        current_status = serializer.instance.status
        if self.request.user.is_staff:
            from apps.administration.models import AdminLog
            campaign = serializer.save(status=current_status)
            AdminLog.objects.create(actor=self.request.user, action="admin_ad_campaign_updated", target_type="AdCampaign", target_id=str(campaign.pk), metadata={"changed_fields": sorted(serializer.validated_data.keys())})
            return
        review_status = current_status if current_status in (AdCampaign.Status.PENDING_PAYMENT, AdCampaign.Status.PENDING_REVIEW) else AdCampaign.Status.PENDING_REVIEW
        serializer.save(status=review_status)

    def perform_destroy(self, instance):
        from apps.payments.models import Payment
        if instance.payments.filter(status=Payment.Status.INITIALIZED).exists():
            raise ValidationError({"status": "A Paystack checkout is still open. Wait for its result before archiving this campaign."})
        if not self.request.user.is_staff and instance.payments.filter(status=Payment.Status.SUCCESS).exists():
            raise PermissionDenied("This campaign has a successful payment. Contact Tarmac Support to request cancellation and review any refund.")
        if self.request.user.is_staff:
            from apps.administration.models import AdminLog
            AdminLog.objects.create(actor=self.request.user, action="ad_campaign_archived", target_type="AdCampaign", target_id=str(instance.pk), metadata={"payment_records_preserved": instance.payments.count(), "previous_status": instance.status})
        elif instance.advertiser_id != self.request.user.id:
            raise PermissionDenied("You can only delete your own ad campaign.")
        else:
            from apps.administration.models import AdminLog
            AdminLog.objects.create(actor=self.request.user, action="advertiser_ad_campaign_cancelled", target_type="AdCampaign", target_id=str(instance.pk), metadata={"previous_status": instance.status})
        instance.status = AdCampaign.Status.CANCELLED
        instance.save(update_fields=("status", "updated_at"))
        AdSlotHold.objects.filter(campaign=instance).delete()
        placements = list(instance.ad_placements.all()) or ([instance.ad_placement] if instance.ad_placement_id else [])
        if instance.starts_at and instance.ends_at:
            for placement in placements:
                transaction.on_commit(lambda slot=placement: _offer_next_waitlisted(slot, instance.starts_at, instance.ends_at))


    @action(detail=True, methods=("post",), permission_classes=(IsAdminOrAbove,))
    @transaction.atomic
    def approve(self, request, pk=None):
        campaign = get_object_or_404(AdCampaign.objects.select_for_update(of=("self",)).select_related("business_advertiser", "ad_placement", "package", "advertiser"), pk=pk)
        from apps.payments.models import Payment
        payment_required = campaign.payment_required_snapshot
        paid = Payment.objects.filter(advertisement=campaign, status=Payment.Status.SUCCESS, amount_kobo=campaign.price_kobo).exclude(refund_status__in=(Payment.RefundStatus.PENDING, Payment.RefundStatus.PROCESSING, Payment.RefundStatus.NEEDS_ATTENTION, Payment.RefundStatus.PROCESSED)).exists()
        if campaign.status != AdCampaign.Status.PENDING_REVIEW:
            raise ValidationError({"status": "Only campaigns awaiting review can be approved."})
        if payment_required and not paid:
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
            if paid:
                for placement in locked_placements:
                    AdSlotHold.objects.update_or_create(campaign=campaign, placement=placement, defaults={"starts_at": booking_starts_at, "ends_at": booking_ends_at, "expires_at": booking_ends_at, "is_confirmed": True})
        campaign.save(update_fields=("status", "approved_at", "starts_at", "ends_at", "updated_at"))
        if campaign.package_id and campaign.advertiser_id:
            successful_payment = Payment.objects.filter(advertisement=campaign, status=Payment.Status.SUCCESS).exclude(refund_status__in=(Payment.RefundStatus.PENDING, Payment.RefundStatus.PROCESSING, Payment.RefundStatus.NEEDS_ATTENTION, Payment.RefundStatus.PROCESSED)).order_by("-paid_at").first()
            from apps.administration.models import StudentSubscription
            StudentSubscription.objects.get_or_create(ad_campaign=campaign, defaults={"user": campaign.advertiser, "plan": campaign.package, "amount_kobo": successful_payment.amount_kobo if successful_payment else 0, "payment_reference": successful_payment.reference if successful_payment else None, "starts_at": campaign.starts_at, "ends_at": campaign.ends_at})
        from apps.administration.models import AdminLog
        AdminLog.objects.create(actor=request.user, action="ad_campaign_approved", target_type="AdCampaign", target_id=str(campaign.pk), metadata={"capacity_override": override, "override_reason": override_reason if override else ""})
        if campaign.business_advertiser_id:
            from apps.notifications.email import send_tarmac_email
            advertiser = campaign.business_advertiser
            token = secrets.token_urlsafe(32)
            campaign.management_token_hash = _token_hash(token)
            campaign.save(update_fields=("management_token_hash", "updated_at"))
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

    @action(detail=True, methods=("post",), permission_classes=(IsAdminOrAbove,))
    @transaction.atomic
    def reject(self, request, pk=None):
        campaign = AdCampaign.objects.select_for_update(of=("self",)).select_related("business_advertiser", "advertiser").get(pk=pk)
        if campaign.status == AdCampaign.Status.ACTIVE:
            raise ValidationError({"status": "Pause an active campaign before rejecting it."})
        if campaign.status in (AdCampaign.Status.REJECTED, AdCampaign.Status.CANCELLED, AdCampaign.Status.ENDED):
            raise ValidationError({"status": "This campaign is already closed."})
        reason = str(request.data.get("reason", "")).strip()
        if len(reason) < 5:
            raise ValidationError({"reason": "Give the advertiser a clear reason (at least 5 characters)."})
        from apps.payments.models import Payment
        successful_payments = list(Payment.objects.select_for_update().filter(advertisement=campaign, status=Payment.Status.SUCCESS).order_by("created_at"))
        if not successful_payments and Payment.objects.filter(advertisement=campaign, status=Payment.Status.INITIALIZED).exists():
            raise ValidationError({"payment": "A Paystack checkout is still open. Wait for its result before rejecting this campaign."})
        refund_requested = request.data.get("refund")
        if successful_payments and not isinstance(refund_requested, bool):
            raise ValidationError({"refund": "Choose whether to refund the successful campaign payment."})
        refund_results = []
        if successful_payments and refund_requested:
            from apps.payments.refunds import request_paystack_refund
            refund_results = [request_paystack_refund(payment, reason=reason, actor=request.user) for payment in successful_payments]
        campaign.status = AdCampaign.Status.REJECTED
        campaign.reviewer_note = reason
        campaign.save(update_fields=("status", "reviewer_note", "updated_at"))
        AdSlotHold.objects.filter(campaign=campaign).delete()
        placements = list(campaign.ad_placements.all()) or ([campaign.ad_placement] if campaign.ad_placement_id else [])
        if campaign.starts_at and campaign.ends_at:
            for placement in placements:
                transaction.on_commit(lambda slot=placement: _offer_next_waitlisted(slot, campaign.starts_at, campaign.ends_at))
        refund_states = [item["status"] for item in refund_results]
        if not successful_payments:
            refund_message = " No payment had been completed, so no refund is due."
        elif not refund_requested:
            refund_message = " No refund was requested. Contact Tarmac Support if you have a payment question."
        elif any(value in ("failed", "needs_attention") for value in refund_states):
            refund_message = " We could not complete the Paystack refund request automatically. Tarmac Support will follow up with you."
        elif refund_states and all(value == "processed" for value in refund_states):
            refund_message = " Paystack has processed your refund. Your bank may take several business days to show the funds."
        else:
            refund_message = " Paystack accepted the refund request. Your bank may take several business days to return the funds."
        if campaign.business_advertiser_id:
            from apps.notifications.email import send_tarmac_email
            token = secrets.token_urlsafe(32)
            campaign.management_token_hash = _token_hash(token)
            campaign.save(update_fields=("management_token_hash", "updated_at"))
            message = f"Your campaign ‘{campaign.title}’ was not approved. Reason: {reason}.{refund_message} You can review the decision and payment status using your private campaign link."
            manage_url = f"{os.getenv('FRONTEND_ORIGIN', 'http://localhost:3000').split(',')[0].rstrip('/')}/advertise/manage?token={token}"
            transaction.on_commit(lambda: send_tarmac_email(subject="Update on your Tarmac advertisement", text_body=message, recipient=campaign.business_advertiser.email, recipient_name=campaign.business_advertiser.contact_name, action_url=manage_url, action_label="Open campaign status"))
        elif campaign.advertiser_id:
            from apps.notifications.services import notify_user
            notify_user(campaign.advertiser, "Your Tarmac advertisement was not approved", f"Campaign: {campaign.title}. Reason: {reason}.{refund_message}", {"kind": "ad_rejected", "campaign_id": campaign.pk, "refund_status": refund_results[0]["status"] if refund_results else ""}, f"ad-rejected:{campaign.pk}")
        from apps.administration.models import AdminLog
        AdminLog.objects.create(actor=request.user, action="ad_campaign_rejected", target_type="AdCampaign", target_id=str(campaign.pk), metadata={"reason": reason, "refund_requested": bool(refund_requested), "refund_results": refund_results})
        return Response({"status": campaign.status, "reason": reason, "refund_results": refund_results})

    @action(detail=True, methods=("post",), permission_classes=(IsAdminOrAbove,))
    def pause(self, request, pk=None):
        campaign = self.get_object()
        if campaign.status != AdCampaign.Status.ACTIVE:
            raise ValidationError({"status": "Only active campaigns can be paused."})
        campaign.status = AdCampaign.Status.PAUSED
        campaign.save(update_fields=("status", "updated_at"))
        from apps.administration.models import AdminLog
        AdminLog.objects.create(actor=request.user, action="ad_campaign_paused", target_type="AdCampaign", target_id=str(campaign.pk), metadata={})
        return Response({"status": campaign.status})

    @action(detail=True, methods=("post",), permission_classes=(IsAdminOrAbove,))
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
        return optimized_image_response(campaign.image)

    @action(detail=True, methods=("get",), url_path="click")
    def click(self, request, pk=None):
        campaign = self.get_object()
        if campaign.status != AdCampaign.Status.ACTIVE:
            raise PermissionDenied("This ad is not active.")
        from config.url_validation import is_safe_external_url
        if campaign.target_url and not is_safe_external_url(campaign.target_url, require_https=not settings.DEBUG):
            raise PermissionDenied("This advertisement has an invalid destination link.")
        AdCampaign.objects.filter(pk=campaign.pk, status=AdCampaign.Status.ACTIVE).update(click_count=F("click_count") + 1)
        return redirect(campaign.target_url or "/")

    @action(detail=True, methods=("post",), url_path="impression")
    def impression(self, request, pk=None):
        campaign = self.get_object()
        if campaign.status != AdCampaign.Status.ACTIVE:
            raise PermissionDenied("This ad is not active.")
        if request.user.is_authenticated:
            impression, created = AdImpression.objects.get_or_create(campaign=campaign, user=request.user, day=timezone.localdate())
        else:
            homepage_campaign = (
                "homepage" in (campaign.pages or [])
                or campaign.placement == AdCampaign.Placement.HOMEPAGE
                or (campaign.ad_placement_id and campaign.ad_placement.page == "homepage")
                or campaign.ad_placements.filter(page="homepage").exists()
            )
            if not homepage_campaign:
                raise PermissionDenied("Guest impressions are only counted for homepage advertisements.")
            try:
                visitor_id = str(uuid.UUID(str(request.data.get("visitor_id", ""))))
            except (TypeError, ValueError, AttributeError):
                raise ValidationError({"visitor_id": "A valid anonymous visitor ID is required."})
            visitor_hash = hmac.new(settings.SECRET_KEY.encode("utf-8"), visitor_id.encode("ascii"), hashlib.sha256).hexdigest()
            impression, created = AdImpression.objects.get_or_create(campaign=campaign, user=None, visitor_hash=visitor_hash, day=timezone.localdate())
        if created:
            AdCampaign.objects.filter(pk=campaign.pk, status=AdCampaign.Status.ACTIVE).update(impression_count=F("impression_count") + 1)
        return Response({"tracked": created})

    @action(detail=False, methods=("get",), url_path="saved")
    def saved(self, request):
        ids = AdBookmark.objects.filter(user=request.user, campaign__status=AdCampaign.Status.ACTIVE).values_list("campaign_id", flat=True)
        return Response({"ids": list(ids)})

    @action(detail=True, methods=("post",), url_path="toggle-saved")
    def toggle_saved(self, request, pk=None):
        campaign = self.get_object()
        if campaign.status != AdCampaign.Status.ACTIVE:
            raise PermissionDenied("Only active advertisements can be saved.")
        bookmark, created = AdBookmark.objects.get_or_create(user=request.user, campaign=campaign)
        if not created:
            bookmark.delete()
        return Response({"saved": created, "campaign_id": campaign.pk})


def models_q_start(now):
    from django.db.models import Q
    return Q(starts_at__isnull=True) | Q(starts_at__lte=now)


def models_q_end(now):
    from django.db.models import Q
    return Q(ends_at__isnull=True) | Q(ends_at__gt=now)

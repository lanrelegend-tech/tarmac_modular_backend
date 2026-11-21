from datetime import datetime, time, timedelta

from django.db.models import Q
from django.utils import timezone

from .models import AdCampaign, AdPlacement, AdSlotHold


BOOKING_STATUSES = (AdCampaign.Status.ACTIVE, AdCampaign.Status.SCHEDULED, AdCampaign.Status.PAUSED)


def local_date_window(start_date, end_date):
    tz = timezone.get_current_timezone()
    starts_at = timezone.make_aware(datetime.combine(start_date, time.min), tz)
    ends_at = timezone.make_aware(datetime.combine(end_date + timedelta(days=1), time.min), tz)
    return starts_at, ends_at


def get_slot_availability(placement, starts_at, ends_at, exclude_campaign_id=None):
    campaigns = AdCampaign.objects.filter(
        Q(ad_placement=placement) | Q(ad_placements=placement),
        status__in=BOOKING_STATUSES,
        starts_at__lt=ends_at,
        ends_at__gt=starts_at,
    )
    if exclude_campaign_id:
        campaigns = campaigns.exclude(pk=exclude_campaign_id)
    occupied = campaigns.distinct().count()
    now = timezone.now()
    holds = AdSlotHold.objects.filter(
        placement=placement,
        expires_at__gt=now,
        starts_at__lt=ends_at,
        ends_at__gt=starts_at,
    ).filter(Q(is_confirmed=False) | Q(is_confirmed=True, campaign__status=AdCampaign.Status.PENDING_REVIEW))
    if exclude_campaign_id:
        holds = holds.exclude(campaign_id=exclude_campaign_id)
    occupied += holds.count()
    capacity = 1 if placement.is_exclusive else placement.max_slots
    remaining = max(0, capacity - occupied)
    state = "fully_booked" if remaining == 0 else "limited" if remaining <= placement.limited_slots_threshold else "available"
    return {"state": state, "capacity": capacity, "occupied": occupied, "remaining": remaining}

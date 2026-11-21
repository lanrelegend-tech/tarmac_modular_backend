from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.ads.models import AdCampaign, AdSlotHold, AdWaitlistEntry
from apps.ads.views import _offer_next_waitlisted


class Command(BaseCommand):
    help = "Activate scheduled ads, expire ended campaigns and holds, and advance eligible waitlists."

    def handle(self, *args, **options):
        now = timezone.now()
        expired_windows = []
        with transaction.atomic():
            scheduled = AdCampaign.objects.filter(status=AdCampaign.Status.SCHEDULED, starts_at__lte=now, ends_at__gt=now)
            activated = scheduled.update(status=AdCampaign.Status.ACTIVE, updated_at=now)
            ended_campaigns = list(AdCampaign.objects.filter(
                status__in=(AdCampaign.Status.ACTIVE, AdCampaign.Status.SCHEDULED, AdCampaign.Status.PAUSED),
                ends_at__lte=now,
            ).prefetch_related("ad_placements").select_related("ad_placement"))
            for campaign in ended_campaigns:
                placements = list(campaign.ad_placements.all()) or ([campaign.ad_placement] if campaign.ad_placement_id else [])
                expired_windows.extend((placement, campaign.starts_at, campaign.ends_at) for placement in placements if campaign.starts_at and campaign.ends_at)
                campaign.status = AdCampaign.Status.ENDED
                campaign.save(update_fields=("status", "updated_at"))

            expired_holds = list(AdSlotHold.objects.filter(is_confirmed=False, expires_at__lte=now).select_related("placement", "campaign"))
            for hold in expired_holds:
                expired_windows.append((hold.placement, hold.starts_at, hold.ends_at))
            AdSlotHold.objects.filter(is_confirmed=False, expires_at__lte=now).delete()

            expired_offers = list(AdWaitlistEntry.objects.filter(status=AdWaitlistEntry.Status.OFFERED, offer_expires_at__lte=now).select_related("placement"))
            expired_windows.extend((entry.placement, entry.starts_at, entry.ends_at) for entry in expired_offers)
            AdWaitlistEntry.objects.filter(status=AdWaitlistEntry.Status.OFFERED, offer_expires_at__lte=now).update(
                status=AdWaitlistEntry.Status.CANCELLED,
                offer_token_hash="",
                updated_at=now,
            )

        offered = 0
        seen = set()
        for placement, starts_at, ends_at in expired_windows:
            key = (placement.pk, starts_at, ends_at)
            if key in seen:
                continue
            seen.add(key)
            _offer_next_waitlisted(placement, starts_at, ends_at)
            offered += 1

        self.stdout.write(self.style.SUCCESS(
            f"Activated {activated} scheduled campaign(s), ended {len(ended_campaigns)} campaign(s), "
            f"expired {len(expired_holds)} payment hold(s), and checked {offered} waitlist window(s)."
        ))

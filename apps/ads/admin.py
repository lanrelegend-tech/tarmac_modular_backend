from datetime import timedelta

from django.contrib import admin, messages
from django.utils import timezone

from apps.administration.models import PlatformSettings
from apps.notifications.services import notify_user
from apps.payments.models import Payment
from .models import AdCampaign


@admin.register(AdCampaign)
class AdCampaignAdmin(admin.ModelAdmin):
    list_display = ("title", "advertiser", "placement", "status", "click_count", "starts_at", "ends_at", "created_at")
    list_filter = ("status", "placement", "created_at")
    search_fields = ("title", "advertiser__email")
    readonly_fields = ("advertiser", "status", "created_at", "updated_at")
    actions = ("approve_campaigns", "pause_campaigns", "reject_campaigns")

    @admin.action(description="Approve paid (or free) campaigns for 30 days")
    def approve_campaigns(self, request, queryset):
        settings = PlatformSettings.get_solo()
        fee_required = settings.ad_fee_enabled and settings.paystack_enabled
        approved = 0
        for ad in queryset.select_related("advertiser"):
            paid = Payment.objects.filter(advertisement=ad, status=Payment.Status.SUCCESS).exists()
            if ad.status != AdCampaign.Status.PENDING_REVIEW or (fee_required and not paid):
                continue
            if not ad.advertiser.is_active or ad.advertiser.is_suspended or not ad.advertiser.is_student_verified:
                continue
            ad.status = AdCampaign.Status.ACTIVE
            ad.starts_at = timezone.now()
            ad.ends_at = ad.starts_at + timedelta(days=30)
            ad.save(update_fields=("status", "starts_at", "ends_at", "updated_at"))
            notify_user(ad.advertiser, "Ad campaign approved", f"Your ad '{ad.title}' is now active for 30 days.",
                        {"kind": "ad_approved", "title": ad.title}, f"ad-approved:{ad.pk}")
            approved += 1
        self.message_user(request, f"Approved {approved} campaign(s).", messages.SUCCESS)

    @admin.action(description="Pause selected campaigns")
    def pause_campaigns(self, request, queryset):
        count = queryset.filter(status=AdCampaign.Status.ACTIVE).update(status=AdCampaign.Status.PAUSED)
        self.message_user(request, f"Paused {count} campaign(s).", messages.SUCCESS)

    @admin.action(description="Reject selected campaigns")
    def reject_campaigns(self, request, queryset):
        count = queryset.exclude(status=AdCampaign.Status.ACTIVE).update(status=AdCampaign.Status.REJECTED)
        self.message_user(request, f"Rejected {count} campaign(s).", messages.SUCCESS)

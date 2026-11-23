from django.contrib import admin

from .models import AdCampaign, AdImpression


@admin.register(AdCampaign)
class AdCampaignAdmin(admin.ModelAdmin):
    list_display = (
        "title", "business_name", "package", "price_kobo", "status",
        "impression_count", "click_count", "starts_at", "ends_at", "created_at",
    )
    list_filter = ("status", "placement", "created_at")
    search_fields = ("title", "business_name", "advertiser__email", "business_advertiser__email")
    readonly_fields = (
        "advertiser", "business_advertiser", "status", "package",
        "plan_name_snapshot", "plan_price_kobo", "payment_required_snapshot",
        "price_kobo", "duration_days", "pages", "ad_placement", "ad_placements",
        "starts_at", "ends_at", "approved_at", "click_count", "impression_count",
        "created_at", "updated_at",
    )
    actions = None

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AdImpression)
class AdImpressionAdmin(admin.ModelAdmin):
    list_display = ("campaign", "user", "day", "created_at")
    list_filter = ("day",)
    search_fields = ("campaign__title", "user__email")
    readonly_fields = ("campaign", "user", "visitor_hash", "day", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

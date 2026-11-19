from django.contrib import admin
from django.conf import settings
from .models import UserReport,Announcement,AdminLog,PlatformSettings

@admin.register(PlatformSettings)
class PlatformSettingsAdmin(admin.ModelAdmin):
    fields = ("maintenance_mode", "maintenance_message", "marketplace_enabled", "lost_found_enabled", "messaging_enabled", "ads_enabled", "paystack_enabled", "listing_fee_enabled", "listing_fee_kobo", "ad_fee_enabled", "ad_fee_kobo", "updated_at")
    readonly_fields = ("updated_at",)
    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser and super().has_change_permission(request, obj)
    def has_add_permission(self, request):
        return request.user.is_superuser and not PlatformSettings.objects.filter(pk=1).exists() and super().has_add_permission(request)
    def has_delete_permission(self, request, obj=None):
        return False
    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        initial.setdefault("paystack_enabled", settings.PAYSTACK_ENABLED_DEFAULT)
        return initial
    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        AdminLog.objects.create(actor=request.user, action="platform_settings_updated", target_type="PlatformSettings", target_id="1", metadata={key:getattr(obj, key) for key in ("maintenance_mode", "marketplace_enabled", "lost_found_enabled", "messaging_enabled", "ads_enabled", "paystack_enabled", "listing_fee_enabled", "listing_fee_kobo", "ad_fee_enabled", "ad_fee_kobo")})

admin.site.register([UserReport,Announcement,AdminLog])

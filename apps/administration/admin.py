from django.contrib import admin
from django.conf import settings
from .models import UserReport,Announcement,AdminLog,PlatformSettings,SupportTicket,SupportMessage,SupportAttachment,SubscriptionPlan,StudentSubscription,EmergencyContact

@admin.register(PlatformSettings)
class PlatformSettingsAdmin(admin.ModelAdmin):
    fields = ("site_name", "site_logo", "tagline", "support_email", "contact_email", "terms_text", "privacy_text", "community_guidelines_text", "maintenance_mode", "maintenance_message", "registration_enabled", "marketplace_enabled", "lost_found_enabled", "jobs_enabled", "accommodation_enabled", "accommodation_listing_duration_days", "external_job_submissions_enabled", "job_listing_approval_required", "messaging_enabled", "ads_enabled", "homepage_ads_enabled", "marketplace_ads_enabled", "homepage_lost_found_enabled", "ad_slide_duration_seconds", "paystack_enabled", "listing_fee_enabled", "listing_fee_kobo", "sponsored_products_enabled", "sponsored_product_fee_enabled", "sponsored_product_fee_kobo", "sponsored_product_duration_days", "ad_fee_enabled", "ad_fee_kobo", "subscription_fee_enabled", "listing_approval_required", "max_product_images", "student_verification_enabled", "require_student_id", "email_verification_required", "auto_approve_students", "featured_products_enabled", "reviews_enabled", "review_moderation_enabled", "announcements_enabled", "email_notifications_enabled", "report_notifications_enabled", "updated_at")
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

@admin.register(SupportTicket)
class SupportTicketAdmin(admin.ModelAdmin):
    list_display = ("subject", "user", "category", "status", "priority", "updated_at")
    list_filter = ("status", "priority", "category")
    search_fields = ("subject", "user__email", "messages__body")

@admin.register(SupportMessage)
class SupportMessageAdmin(admin.ModelAdmin):
    list_display = ("ticket", "sender", "created_at")
    readonly_fields = ("ticket", "sender", "body", "created_at")
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False

admin.site.register([UserReport,Announcement,AdminLog,SupportAttachment,SubscriptionPlan,StudentSubscription])


@admin.register(EmergencyContact)
class EmergencyContactAdmin(admin.ModelAdmin):
    list_display = ("name", "phone_number", "location", "is_active", "sort_order", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("name", "phone_number", "description", "location")
    ordering = ("sort_order", "name")

from django.contrib import admin

from .models import AccommodationImage, AccommodationListing, HostelAgentRequest


class AccommodationImageInline(admin.TabularInline):
    model = AccommodationImage
    extra = 0


@admin.register(AccommodationListing)
class AccommodationListingAdmin(admin.ModelAdmin):
    list_display = ("title", "post_type", "location_area", "status", "owner", "created_at")
    list_filter = ("post_type", "status", "accommodation_type")
    search_fields = ("title", "description", "location_area", "owner__email", "owner__username", "owner__public_id")
    readonly_fields = ("created_at", "updated_at", "reviewed_at")
    inlines = (AccommodationImageInline,)


@admin.register(HostelAgentRequest)
class HostelAgentRequestAdmin(admin.ModelAdmin):
    list_display = ("user", "status", "created_at", "reviewed_by", "reviewed_at")
    list_filter = ("status",)
    search_fields = ("user__email", "user__username", "user__public_id", "reason")
    readonly_fields = ("created_at", "updated_at", "reviewed_at")

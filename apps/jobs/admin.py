from django.contrib import admin
from .models import JobCategory, JobListing, JobReport


@admin.register(JobCategory)
class JobCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "category_type", "is_active")
    list_filter = ("category_type", "is_active")
    search_fields = ("name",)
    prepopulated_fields = {"slug": ("name",)}


@admin.register(JobListing)
class JobListingAdmin(admin.ModelAdmin):
    list_display = ("title", "source", "owner", "business_name", "status", "deadline", "created_at")
    list_filter = ("source", "kind", "status", "category")
    search_fields = ("title", "business_name", "business_email", "owner__email")
    readonly_fields = ("created_at", "updated_at", "reviewed_at", "management_token_hash")


@admin.register(JobReport)
class JobReportAdmin(admin.ModelAdmin):
    list_display = ("listing", "reporter", "reason", "status", "created_at")
    list_filter = ("reason", "status")
    search_fields = ("listing__title", "reporter__email", "details")

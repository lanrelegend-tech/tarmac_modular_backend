from django.contrib import admin

from .models import SellerReview


@admin.register(SellerReview)
class SellerReviewAdmin(admin.ModelAdmin):
    list_display = ("seller", "reviewer", "order", "rating", "status", "created_at")
    list_filter = ("status", "rating", "created_at")
    search_fields = ("seller__email", "reviewer__email", "comment")
    readonly_fields = ("order", "reviewer", "seller", "rating", "comment", "created_at", "updated_at")
    actions = ("approve_reviews", "hide_reviews")

    @admin.action(description="Approve selected reviews")
    def approve_reviews(self, request, queryset):
        queryset.update(status=SellerReview.Status.APPROVED)

    @admin.action(description="Hide selected reviews")
    def hide_reviews(self, request, queryset):
        queryset.update(status=SellerReview.Status.HIDDEN)

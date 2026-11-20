from datetime import timedelta

from django.contrib import admin, messages
from django.utils import timezone

from apps.notifications.services import notify_user
from apps.payments.models import Payment
from apps.administration.models import PlatformSettings
from apps.accounts.permissions import is_student_feature_eligible
from .models import Category, Product, ProductImage, Favourite


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "is_allowed_for_sale")
    list_filter = ("is_active", "is_allowed_for_sale")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("title", "seller", "category", "status", "price", "created_at")
    list_filter = ("status", "category", "condition", "created_at")
    search_fields = ("title", "seller__email", "seller__matric_number")
    readonly_fields = ("seller", "status", "expires_at", "created_at", "updated_at")
    actions = ("approve_paid_listings", "remove_listings")

    @admin.action(description="Approve paid listings and activate for 30 days")
    def approve_paid_listings(self, request, queryset):
        approved = 0
        settings = PlatformSettings.get_solo()
        fee_required = settings.listing_fee_enabled and settings.paystack_enabled
        for product in queryset.select_related("seller", "category"):
            has_paid = Payment.objects.filter(product=product, status=Payment.Status.SUCCESS).exists()
            eligible = is_student_feature_eligible(product.seller)
            category_allowed = product.category.is_active and product.category.is_allowed_for_sale
            if product.status != Product.Status.PENDING_REVIEW or (fee_required and not has_paid) or not eligible or not category_allowed:
                continue
            product.status = Product.Status.ACTIVE
            product.expires_at = timezone.now() + timedelta(days=30)
            product.save(update_fields=("status", "expires_at", "updated_at"))
            notify_user(product.seller, "Listing approved", f"Your listing '{product.title}' is now active for 30 days.",
                        {"kind": "listing_active", "product_id": product.pk, "title": product.title}, f"listing-active:{product.pk}")
            approved += 1
        self.message_user(request, f"Activated {approved} paid listing(s).", messages.SUCCESS)

    @admin.action(description="Remove selected listings")
    def remove_listings(self, request, queryset):
        count = queryset.exclude(status=Product.Status.REMOVED).update(status=Product.Status.REMOVED)
        self.message_user(request, f"Removed {count} listing(s).", messages.SUCCESS)


admin.site.register([ProductImage, Favourite])

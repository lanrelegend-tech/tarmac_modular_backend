from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import models


class AdCampaign(models.Model):
    class Status(models.TextChoices):
        PENDING_PAYMENT = "pending_payment", "Pending payment"
        PENDING_REVIEW = "pending_review", "Pending school review"
        ACTIVE = "active", "Active"
        PAUSED = "paused", "Paused"
        REJECTED = "rejected", "Rejected"
        ENDED = "ended", "Ended"

    class Placement(models.TextChoices):
        HOMEPAGE = "homepage", "Homepage"
        MARKETPLACE = "marketplace", "Marketplace"
        LOST_FOUND = "lost_found", "Lost and found"
        ANNOUNCEMENTS = "announcements", "Announcements"

    advertiser = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="ad_campaigns")
    package = models.ForeignKey("administration.SubscriptionPlan", null=True, blank=True, on_delete=models.SET_NULL, related_name="ad_campaigns")
    business_name = models.CharField(max_length=120, blank=True)
    pages = models.JSONField(default=list, blank=True)
    display_image = models.CharField(max_length=500, blank=True)
    price_kobo = models.PositiveIntegerField(default=0)
    duration_days = models.PositiveSmallIntegerField(default=30)
    title = models.CharField(max_length=120)
    description = models.CharField(max_length=240, blank=True)
    image = models.ImageField(upload_to="ads/", validators=[FileExtensionValidator(("jpg", "jpeg", "png", "webp"))], blank=True, null=True)
    target_url = models.URLField(max_length=500, blank=True)
    placement = models.CharField(max_length=20, choices=Placement.choices, default=Placement.MARKETPLACE)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING_PAYMENT, db_index=True)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    click_count = models.PositiveBigIntegerField(default=0)
    impression_count = models.PositiveBigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("status", "placement", "starts_at", "ends_at"), name="ad_delivery_lookup_idx")]

    def __str__(self):
        return self.title

class AdImpression(models.Model):
    campaign = models.ForeignKey(AdCampaign, on_delete=models.CASCADE, related_name="impressions")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ad_impressions")
    day = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=("campaign", "user", "day"), name="unique_daily_user_ad_impression")]

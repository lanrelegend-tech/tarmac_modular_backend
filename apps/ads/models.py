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
        MARKETPLACE = "marketplace", "Marketplace"
        LOST_FOUND = "lost_found", "Lost and found"

    advertiser = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="ad_campaigns")
    title = models.CharField(max_length=120)
    description = models.CharField(max_length=240)
    image = models.ImageField(upload_to="ads/", validators=[FileExtensionValidator(("jpg", "jpeg", "png", "webp"))])
    target_url = models.URLField(max_length=500)
    placement = models.CharField(max_length=20, choices=Placement.choices, default=Placement.MARKETPLACE)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING_PAYMENT, db_index=True)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    click_count = models.PositiveBigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("status", "placement", "starts_at", "ends_at"), name="ad_delivery_lookup_idx")]

    def __str__(self):
        return self.title

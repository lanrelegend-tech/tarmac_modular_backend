from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class SellerReview(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending moderation"
        APPROVED = "approved", "Visible"
        HIDDEN = "hidden", "Hidden"

    order = models.OneToOneField(
        "orders.Order", on_delete=models.PROTECT, related_name="seller_review"
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="seller_reviews_written"
    )
    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="seller_reviews_received"
    )
    rating = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    comment = models.TextField(max_length=1200, blank=True)
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("seller", "status", "created_at"), name="seller_review_lookup_idx")]

    def __str__(self):
        return f"{self.rating}/5 review for {self.seller}"

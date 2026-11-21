from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class SellerReview(models.Model):
    class Category(models.TextChoices):
        MARKETPLACE = "marketplace", "Marketplace"
        JOBS = "jobs", "Jobs & Gigs"
    class Status(models.TextChoices):
        PENDING = "pending", "Pending moderation"
        APPROVED = "approved", "Visible"
        HIDDEN = "hidden", "Hidden"

    order = models.OneToOneField("orders.Order", on_delete=models.PROTECT, related_name="seller_review", null=True, blank=True)
    conversation = models.ForeignKey("messaging.Conversation", on_delete=models.PROTECT, related_name="peer_reviews", null=True, blank=True)
    job_engagement = models.ForeignKey("jobs.JobEngagement", on_delete=models.PROTECT, related_name="reviews", null=True, blank=True)
    category = models.CharField(max_length=16, choices=Category.choices, default=Category.MARKETPLACE, db_index=True)
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="seller_reviews_written"
    )
    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="seller_reviews_received"
    )
    identity_type = models.CharField(max_length=12, choices=(("normal", "Normal profile"), ("anonymous", "Anonymous marketplace identity")), default="normal")
    anonymous_identity = models.ForeignKey("accounts.AnonymousMarketplaceIdentity", on_delete=models.SET_NULL, related_name="reviews", null=True, blank=True)
    identity_public_id = models.CharField(max_length=36, blank=True)
    identity_display_name = models.CharField(max_length=150, blank=True)
    reviewer_display_name = models.CharField(max_length=150, blank=True)
    rating = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    comment = models.TextField(max_length=1200, blank=True)
    image = models.ImageField(upload_to="reviews/", blank=True, null=True)
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("seller", "status", "created_at"), name="seller_review_lookup_idx"), models.Index(fields=("identity_public_id", "status", "created_at"), name="review_identity_lookup_idx")]
        constraints = [
            models.UniqueConstraint(fields=("conversation", "reviewer"), condition=models.Q(conversation__isnull=False, category="marketplace"), name="unique_peer_review_conversation_user"),
            models.UniqueConstraint(fields=("job_engagement", "reviewer"), condition=models.Q(job_engagement__isnull=False, category="jobs"), name="unique_job_review_engagement_user"),
        ]

    def __str__(self):
        return f"{self.rating}/5 review for {self.seller}"

    @property
    def sentiment(self):
        if self.rating >= 4:
            return "positive"
        if self.rating == 3:
            return "neutral"
        return "negative"


class ReviewReport(models.Model):
    class Reason(models.TextChoices):
        FAKE = "fake", "Fake or not based on an interaction"
        ABUSIVE = "abusive", "Abusive or harassing"
        PRIVATE = "private_information", "Contains private information"
        SPAM = "spam", "Spam or inappropriate content"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        REVIEWING = "reviewing", "Reviewing"
        RESOLVED = "resolved", "Resolved"
        DISMISSED = "dismissed", "Dismissed"

    review = models.ForeignKey(SellerReview, on_delete=models.CASCADE, related_name="reports")
    reporter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="review_reports")
    reason = models.CharField(max_length=40, choices=Reason.choices)
    details = models.TextField(max_length=2000, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.OPEN, db_index=True)
    admin_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [models.UniqueConstraint(fields=("review", "reporter"), condition=models.Q(status__in=("open", "reviewing")), name="review_one_open_report_per_user")]
        indexes = [models.Index(fields=("status", "created_at"), name="review_report_queue_idx")]

    def __str__(self):
        return f"Report #{self.pk} for review #{self.review_id}"

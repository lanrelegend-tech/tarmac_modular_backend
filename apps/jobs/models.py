from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone
from uuid import uuid4


class JobCategory(models.Model):
    name = models.CharField(max_length=80, unique=True)
    slug = models.SlugField(unique=True)
    category_type = models.CharField(max_length=12, choices=(("job", "Jobs"), ("service", "Services")), default="job")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("category_type", "name")

    def __str__(self):
        return self.name


class JobListing(models.Model):
    public_id = models.UUIDField(default=uuid4, unique=True, editable=False, db_index=True)
    class Source(models.TextChoices):
        STUDENT = "student", "Student"
        BUSINESS = "business", "External business"

    class Kind(models.TextChoices):
        JOB = "job", "Job or internship"
        SERVICE = "service", "Gig or service"

    class ServiceIntent(models.TextChoices):
        OFFER = "offer", "Offering a service"
        REQUEST = "request", "Looking for a service"

    class Engagement(models.TextChoices):
        PART_TIME = "part_time", "Part-time"
        FULL_TIME = "full_time", "Full-time"
        INTERNSHIP = "internship", "Internship"
        TEMPORARY = "temporary", "Temporary"
        FREELANCE = "freelance", "Freelance"
        SERVICE = "service", "Student service"

    class PayType(models.TextChoices):
        FIXED = "fixed", "Fixed amount"
        HOURLY = "hourly", "Per hour"
        NEGOTIABLE = "negotiable", "Negotiable"
        UNPAID = "unpaid", "Unpaid"

    class Status(models.TextChoices):
        PENDING_EMAIL = "pending_email", "Awaiting email confirmation"
        PENDING_REVIEW = "pending_review", "Pending review"
        ACTIVE = "active", "Active"
        PAUSED = "paused", "Paused"
        FILLED = "filled", "Filled"
        EXPIRED = "expired", "Expired"
        REJECTED = "rejected", "Rejected"
        REMOVED = "removed", "Removed"
        DELETION_REQUESTED = "deletion_requested", "Deletion requested"

    source = models.CharField(max_length=12, choices=Source.choices, default=Source.STUDENT, db_index=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="job_listings")
    identity_type = models.CharField(max_length=12, choices=(("normal", "Normal profile"), ("anonymous", "Anonymous marketplace identity")), default="normal", db_index=True)
    anonymous_identity = models.ForeignKey("accounts.AnonymousMarketplaceIdentity", null=True, blank=True, on_delete=models.SET_NULL, related_name="job_listings")
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.JOB)
    service_intent = models.CharField(max_length=8, choices=ServiceIntent.choices, default=ServiceIntent.OFFER, db_index=True)
    engagement = models.CharField(max_length=16, choices=Engagement.choices, default=Engagement.PART_TIME)
    category = models.ForeignKey(JobCategory, on_delete=models.PROTECT, related_name="listings")
    campus = models.CharField(max_length=120, db_index=True, blank=True)
    title = models.CharField(max_length=180)
    description = models.TextField(max_length=8000)
    requirements = models.TextField(max_length=4000, blank=True)
    pay_type = models.CharField(max_length=16, choices=PayType.choices, default=PayType.NEGOTIABLE)
    pay_min = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    pay_max = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    location = models.CharField(max_length=180, blank=True)
    working_hours = models.CharField(max_length=180, blank=True)
    duration = models.CharField(max_length=120, blank=True)
    positions = models.PositiveSmallIntegerField(default=1)
    deadline = models.DateField(db_index=True)
    application_instructions = models.TextField(max_length=2000, blank=True)
    application_url = models.URLField(max_length=500, blank=True)

    # External business details; only business_name and explicitly approved
    # application instructions/URL are public. Direct contact details stay private.
    business_name = models.CharField(max_length=180, blank=True)
    business_contact_name = models.CharField(max_length=120, blank=True)
    business_email = models.EmailField(blank=True)
    business_phone = models.CharField(max_length=30, blank=True)
    business_website = models.URLField(max_length=500, blank=True)
    business_description = models.TextField(max_length=1500, blank=True)
    management_token_hash = models.CharField(max_length=64, blank=True)
    management_token_expires_at = models.DateTimeField(null=True, blank=True)
    management_token_revoked_at = models.DateTimeField(null=True, blank=True)
    management_last_used_at = models.DateTimeField(null=True, blank=True)
    business_email_token_hash = models.CharField(max_length=64, blank=True)
    business_email_token_expires_at = models.DateTimeField(null=True, blank=True)
    business_email_verified_at = models.DateTimeField(null=True, blank=True)

    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PENDING_REVIEW, db_index=True)
    admin_note = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="reviewed_job_listings")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("status", "deadline", "created_at"), name="jobs_active_deadline_idx"),
            models.Index(fields=("kind", "category", "status"), name="jobs_kind_category_idx"),
            models.Index(fields=("source", "status", "created_at"), name="jobs_source_status_idx"),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(pay_min__isnull=True) | Q(pay_max__isnull=True) | Q(pay_max__gte=models.F("pay_min")), name="jobs_pay_range_valid"),
        ]

    def __str__(self):
        return self.title

    def expire_if_due(self):
        if self.status == self.Status.ACTIVE and self.deadline < timezone.localdate():
            self.status = self.Status.EXPIRED
            self.closed_at = timezone.now()
            self.save(update_fields=("status", "closed_at", "updated_at"))
            return True
        return False


class JobReport(models.Model):
    class Reason(models.TextChoices):
        SCAM = "scam", "Fraud or suspicious payment request"
        FAKE = "fake", "Fake job or business"
        MISLEADING = "misleading", "Misleading pay or conditions"
        INAPPROPRIATE = "inappropriate", "Inappropriate requirements or content"
        HARASSMENT = "harassment", "Harassment or abuse"
        OTHER = "other", "Other concern"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        REVIEWING = "reviewing", "Reviewing"
        RESOLVED = "resolved", "Resolved"
        DISMISSED = "dismissed", "Dismissed"

    listing = models.ForeignKey(JobListing, on_delete=models.CASCADE, related_name="reports")
    reporter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="job_reports")
    reason = models.CharField(max_length=20, choices=Reason.choices)
    details = models.TextField(max_length=3000, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.OPEN, db_index=True)
    admin_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [models.UniqueConstraint(fields=("listing", "reporter"), condition=Q(status__in=("open", "reviewing")), name="jobs_one_open_report_per_user")]

    def __str__(self):
        return f"Job report #{self.pk} ({self.reason})"


class JobEngagement(models.Model):
    """A two-sided record that a student job/gig was undertaken and completed."""
    class Status(models.TextChoices):
        OFFERED = "offered", "Awaiting acceptance"
        ACTIVE = "active", "In progress"
        COMPLETION_REQUESTED = "completion_requested", "Awaiting completion confirmation"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    listing = models.ForeignKey(JobListing, on_delete=models.PROTECT, related_name="engagements")
    conversation = models.OneToOneField("messaging.Conversation", on_delete=models.PROTECT, related_name="job_engagement")
    poster = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="job_engagements_posted")
    applicant = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="job_engagements_accepted")
    initiated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="job_engagements_initiated")
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.OFFERED, db_index=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    completion_requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="job_engagement_completions_requested")
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-updated_at",)
        indexes = [models.Index(fields=("status", "updated_at"), name="job_engagement_status_idx")]

    def __str__(self):
        return f"{self.listing_id}: {self.poster_id} / {self.applicant_id} ({self.status})"

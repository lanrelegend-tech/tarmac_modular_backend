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
        SCHEDULED = "scheduled", "Scheduled"
        CANCELLED = "cancelled", "Cancelled"
        ENDED = "ended", "Ended"

    class Placement(models.TextChoices):
        HOMEPAGE = "homepage", "Homepage"
        MARKETPLACE = "marketplace", "Marketplace"
        LOST_FOUND = "lost_found", "Lost and found"
        ANNOUNCEMENTS = "announcements", "Announcements"
        JOBS = "jobs", "Jobs and gigs"

    advertiser = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="ad_campaigns", null=True, blank=True)
    business_advertiser = models.ForeignKey("ads.AdAdvertiser", on_delete=models.PROTECT, related_name="campaigns", null=True, blank=True)
    waitlist_entry = models.ForeignKey("ads.AdWaitlistEntry", on_delete=models.SET_NULL, related_name="campaigns", null=True, blank=True)
    management_token_hash = models.CharField(max_length=64, blank=True, db_index=True)
    ad_placement = models.ForeignKey("ads.AdPlacement", on_delete=models.PROTECT, related_name="campaigns", null=True, blank=True)
    ad_placements = models.ManyToManyField("ads.AdPlacement", related_name="multi_page_campaigns", blank=True)
    package = models.ForeignKey("administration.SubscriptionPlan", null=True, blank=True, on_delete=models.SET_NULL, related_name="ad_campaigns")
    # Keep the package terms that the advertiser accepted. The linked plan is
    # editable by admins for future campaigns, so it is not a historical record.
    plan_name_snapshot = models.CharField(max_length=100, blank=True)
    plan_price_kobo = models.PositiveIntegerField(default=0)
    payment_required_snapshot = models.BooleanField(default=False)
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
    approved_at = models.DateTimeField(null=True, blank=True)
    reviewer_note = models.TextField(blank=True)
    advertising_policy_accepted = models.BooleanField(default=False)
    advertising_policy_accepted_at = models.DateTimeField(null=True, blank=True)
    click_count = models.PositiveBigIntegerField(default=0)
    impression_count = models.PositiveBigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("status", "placement", "starts_at", "ends_at"), name="ad_delivery_lookup_idx")]

    def __str__(self):
        return self.title


class AdAdvertiser(models.Model):
    business_name = models.CharField(max_length=160)
    contact_name = models.CharField(max_length=120)
    email = models.EmailField(db_index=True)
    phone = models.CharField(max_length=32)
    location = models.CharField(max_length=160, blank=True)
    website = models.URLField(max_length=500, blank=True)
    management_token_hash = models.CharField(max_length=64, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.business_name} ({self.email})"


class AdPlacement(models.Model):
    class Page(models.TextChoices):
        HOMEPAGE = "homepage", "Homepage"
        MARKETPLACE = "marketplace", "Marketplace"
        ANNOUNCEMENTS = "announcements", "Announcements"
        JOBS = "jobs", "Jobs & Gigs"
        LOST_FOUND = "lost_found", "Lost & Found"

    key = models.SlugField(max_length=80, unique=True)
    page = models.CharField(max_length=20, choices=Page.choices)
    position = models.CharField(max_length=100)
    description = models.CharField(max_length=240, blank=True)
    max_slots = models.PositiveSmallIntegerField(default=5)
    limited_slots_threshold = models.PositiveSmallIntegerField(default=2)
    is_active = models.BooleanField(default=True)
    is_exclusive = models.BooleanField(default=False)
    sort_order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("page", "sort_order", "position")
        constraints = [
            models.CheckConstraint(condition=models.Q(max_slots__gte=1), name="ad_placement_capacity_positive"),
            models.CheckConstraint(condition=models.Q(limited_slots_threshold__lte=models.F("max_slots")), name="ad_placement_limited_within_capacity"),
        ]

    def __str__(self):
        return f"{self.get_page_display()} · {self.position}"


class AdSlotHold(models.Model):
    placement = models.ForeignKey(AdPlacement, on_delete=models.CASCADE, related_name="holds")
    campaign = models.ForeignKey(AdCampaign, on_delete=models.CASCADE, related_name="slot_holds")
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    expires_at = models.DateTimeField(db_index=True)
    is_confirmed = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=("placement", "is_confirmed", "expires_at"), name="ad_hold_capacity_idx")]
        constraints = [models.UniqueConstraint(fields=("campaign", "placement"), name="unique_campaign_ad_slot_hold")]


class AdWaitlistEntry(models.Model):
    class Status(models.TextChoices):
        WAITING = "waiting", "Waiting"
        OFFERED = "offered", "Offer sent"
        BOOKED = "booked", "Booked"
        CANCELLED = "cancelled", "Cancelled"

    advertiser = models.ForeignKey(AdAdvertiser, on_delete=models.CASCADE, related_name="waitlist_entries")
    placement = models.ForeignKey(AdPlacement, on_delete=models.CASCADE, related_name="waitlist_entries")
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.WAITING, db_index=True)
    offer_token_hash = models.CharField(max_length=64, blank=True)
    offer_expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("created_at", "pk")
        constraints = [models.UniqueConstraint(fields=("advertiser", "placement", "starts_at", "ends_at"), name="unique_ad_waitlist_window")]
        indexes = [models.Index(fields=("placement", "status", "created_at"), name="ad_waitlist_queue_idx")]

class AdImpression(models.Model):
    campaign = models.ForeignKey(AdCampaign, on_delete=models.CASCADE, related_name="impressions")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ad_impressions", null=True, blank=True)
    visitor_hash = models.CharField(max_length=64, blank=True, db_index=True)
    day = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("campaign", "user", "day"), condition=models.Q(user__isnull=False), name="unique_daily_user_ad_impression"),
            models.UniqueConstraint(fields=("campaign", "visitor_hash", "day"), condition=~models.Q(visitor_hash=""), name="unique_daily_visitor_ad_impression"),
        ]

from django.conf import settings
from django.db import models
from django.utils import timezone


class AccommodationListing(models.Model):
    class PostType(models.TextChoices):
        SEEK_ROOMMATE = "roommate", "Looking for a roommate"
        SEEK_ACCOMMODATION = "search", "Looking for accommodation"
        OFFER_ACCOMMODATION = "offer", "Offering accommodation"

    class AccommodationType(models.TextChoices):
        HOSTEL_BEDSPACE = "hostel_bedspace", "Hostel bedspace"
        ROOM = "room", "Room"
        SELF_CONTAINED = "self_contained", "Self-contained"
        ROOM_AND_PARLOUR = "room_and_parlour", "Room and parlour"
        FLAT = "flat", "Flat or apartment"
        OTHER = "other", "Other"
        ANY = "any", "Any suitable type"

    class RentPeriod(models.TextChoices):
        MONTH = "month", "Monthly"
        SEMESTER = "semester", "Per semester"
        SESSION = "session", "Per academic session"
        YEAR = "year", "Per year"

    class Status(models.TextChoices):
        PENDING_REVIEW = "pending_review", "Pending review"
        ACTIVE = "active", "Published"
        REJECTED = "rejected", "Rejected"
        REMOVED = "removed", "Removed"
        FILLED = "filled", "No longer available"

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="accommodation_listings")
    campus = models.CharField(max_length=120, db_index=True, blank=True)
    post_type = models.CharField(max_length=12, choices=PostType.choices, db_index=True)
    accommodation_type = models.CharField(max_length=24, choices=AccommodationType.choices, default=AccommodationType.ANY)
    title = models.CharField(max_length=160)
    description = models.TextField(max_length=5000)
    location_area = models.CharField(max_length=160)
    private_address = models.CharField(max_length=240, blank=True)
    rent_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    budget_min = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    budget_max = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    rent_period = models.CharField(max_length=12, choices=RentPeriod.choices, blank=True)
    agency_fee = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    caution_fee = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    service_fee = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    other_fees = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    other_fees_note = models.CharField(max_length=180, blank=True)
    water_available = models.BooleanField(default=False)
    electricity_available = models.BooleanField(default=False)
    road_access = models.BooleanField(default=False)
    security_available = models.BooleanField(default=False)
    bathroom_available = models.BooleanField(default=False)
    kitchen_available = models.BooleanField(default=False)
    furnished = models.BooleanField(default=False)
    current_occupants = models.PositiveSmallIntegerField(null=True, blank=True)
    room_capacity = models.PositiveSmallIntegerField(null=True, blank=True)
    roommates_needed = models.PositiveSmallIntegerField(null=True, blank=True)
    available_from = models.DateField(null=True, blank=True)
    available_until = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING_REVIEW, db_index=True)
    accuracy_confirmed = models.BooleanField(default=False)
    authorization_confirmed = models.BooleanField(default=False)
    reviewer_note = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="reviewed_accommodation_listings")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("status", "post_type", "created_at"), name="accom_status_type_idx"),
            models.Index(fields=("location_area", "status"), name="accom_location_status_idx"),
            models.Index(fields=("accommodation_type", "status"), name="accom_kind_status_idx"),
        ]

    def expire_if_due(self):
        if self.status == self.Status.ACTIVE and self.expires_at and self.expires_at <= timezone.now():
            self.status = self.Status.FILLED
            self.save(update_fields=("status", "updated_at"))
            return True
        return False

    def __str__(self):
        return self.title


class AccommodationImage(models.Model):
    listing = models.ForeignKey(AccommodationListing, on_delete=models.CASCADE, related_name="images")
    image = models.ImageField(upload_to="accommodation/")
    position = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("position", "id")


class HostelAgentRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        REVOKED = "revoked", "Badge revoked"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="hostel_agent_requests")
    reason = models.TextField(max_length=2000)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True)
    admin_note = models.TextField(blank=True, max_length=2000)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="reviewed_hostel_agent_requests")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("status", "created_at"), name="hostel_agent_queue_idx")]
        constraints = [models.UniqueConstraint(fields=("user",), condition=models.Q(status="pending"), name="one_pending_hostel_agent_request")]

    def __str__(self):
        return f"Hostel agent request #{self.pk} ({self.status})"


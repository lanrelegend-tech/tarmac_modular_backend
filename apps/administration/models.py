from django.conf import settings
from django.db import models
from django.core.cache import cache
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible
from django.utils import timezone
from uuid import uuid4
import logging
from config.storage import PrivateCloudinaryImageStorage

logger = logging.getLogger(__name__)


def default_notification_channels():
    return {
        "lost_found": {"email": True, "whatsapp": False},
        "support": {"email": True, "whatsapp": False},
        "official": {"email": True, "whatsapp": False},
        "promotional": {"email": True, "whatsapp": False},
        "account_security": {"email": True, "whatsapp": False},
        "marketplace": {"email": True, "whatsapp": False},
        "payments": {"email": True, "whatsapp": False},
        "accommodation": {"email": True, "whatsapp": False},
    }


def default_campuses():
    return []


def default_campus_areas():
    return {}


def report_evidence_upload_to(instance, filename):
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else "jpg"
    return f"report_evidence/{uuid4().hex}.{extension}"


@deconstructible
class PrivateMediaStorage(FileSystemStorage):
    def __init__(self):
        super().__init__(location=settings.PRIVATE_MEDIA_ROOT)


class UserReport(models.Model):
    class Status(models.TextChoices): OPEN = "open", "Open"; INVESTIGATING = "investigating", "Investigating"; RESOLVED = "resolved", "Resolved"; DISMISSED = "dismissed", "Dismissed"
    reporter=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,related_name="filed_reports"); reported_user=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.SET_NULL,related_name="reports_against")
    product=models.ForeignKey("products.Product",null=True,blank=True,on_delete=models.SET_NULL); lost_found_report=models.ForeignKey("lost_found.LostFoundReport",null=True,blank=True,on_delete=models.SET_NULL)
    marketplace_identity=models.ForeignKey("accounts.AnonymousMarketplaceIdentity",null=True,blank=True,on_delete=models.SET_NULL,related_name="reports")
    target_identity_type=models.CharField(max_length=12,blank=True); target_identity_public_id=models.CharField(max_length=36,blank=True); target_identity_name=models.CharField(max_length=150,blank=True)
    conversation=models.ForeignKey("messaging.Conversation",null=True,blank=True,on_delete=models.SET_NULL,related_name="moderation_reports")
    reason=models.CharField(max_length=100); details=models.TextField(blank=True); is_resolved=models.BooleanField(default=False); status=models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN); reviewer_note=models.TextField(blank=True); assigned_to=models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="assigned_reports"); created_at=models.DateTimeField(auto_now_add=True)


class ReportEvidence(models.Model):
    report = models.ForeignKey(UserReport, on_delete=models.CASCADE, related_name="evidence")
    image = models.ImageField(upload_to=report_evidence_upload_to, storage=PrivateCloudinaryImageStorage())
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at",)

    def __str__(self):
        return f"Evidence for report #{self.report_id}"


class Announcement(models.Model):
    class Audience(models.TextChoices): ALL_VERIFIED="all_verified", "All verified students"; DEPARTMENT="department", "Department"
    class Category(models.TextChoices): EDUCATION="education", "Education"; EMERGENCY="emergency", "Emergency"; ACADEMIC="academic", "Academic"; EXAMINATIONS="examinations", "Examinations"; EVENTS="events", "Events"; SECURITY="security", "Safety & security"; MARKETPLACE="marketplace", "Marketplace"; LOST_FOUND="lost_found", "Lost & found"; JOBS="jobs", "Jobs & gigs"; ACCOMMODATION="accommodation", "Accommodation"; GENERAL="general", "General"
    title=models.CharField(max_length=160); body=models.TextField(); poster_name=models.CharField(max_length=120,default="KWASU"); category=models.CharField(max_length=24,choices=Category.choices,default=Category.GENERAL,db_index=True); image=models.ImageField(upload_to="announcements/", blank=True, null=True); created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT); is_published=models.BooleanField(default=False); is_archived=models.BooleanField(default=False, db_index=True); archived_at=models.DateTimeField(null=True,blank=True); expires_at=models.DateTimeField(null=True,blank=True,db_index=True); pinned=models.BooleanField(default=False); audience=models.CharField(max_length=20,choices=Audience.choices,default=Audience.ALL_VERIFIED); department=models.CharField(max_length=120,blank=True); published_at=models.DateTimeField(null=True,blank=True); delivery_cursor=models.PositiveBigIntegerField(default=0); delivery_completed=models.BooleanField(default=False); recipient_count=models.PositiveBigIntegerField(default=0); created_at=models.DateTimeField(auto_now_add=True)


class AnnouncementImage(models.Model):
    announcement = models.ForeignKey(Announcement, on_delete=models.CASCADE, related_name="gallery_images")
    image = models.ImageField(upload_to="announcements/gallery/")
    sort_order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("sort_order", "pk")


class AnnouncementPublisher(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="announcement_publisher_access")
    granted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="granted_announcement_publisher_access")
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-is_active", "user__email")

    def __str__(self):
        return f"Announcement publisher: {self.user.email}"


class AdminLog(models.Model):
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.SET_NULL); action=models.CharField(max_length=120); target_type=models.CharField(max_length=80,blank=True); target_id=models.CharField(max_length=80,blank=True); metadata=models.JSONField(default=dict,blank=True); created_at=models.DateTimeField(auto_now_add=True)

class PlatformSettings(models.Model):
    site_name = models.CharField(max_length=100, default="Tarmac")
    site_logo = models.ImageField(upload_to="site/", blank=True, null=True)
    tagline = models.CharField(max_length=240, default="The marketplace built for students.")
    support_email = models.EmailField(blank=True)
    contact_email = models.EmailField(blank=True)
    campuses = models.JSONField(default=default_campuses, blank=True)
    campus_areas = models.JSONField(default=default_campus_areas, blank=True)
    registration_enabled = models.BooleanField(default=True)
    listing_approval_required = models.BooleanField(default=True)
    max_product_images = models.PositiveSmallIntegerField(default=5)
    student_verification_enabled = models.BooleanField(default=True)
    require_student_id = models.BooleanField(default=True)
    email_verification_required = models.BooleanField(default=True)
    auto_approve_students = models.BooleanField(default=False)
    featured_products_enabled = models.BooleanField(default=True)
    sponsored_products_enabled = models.BooleanField(default=True)
    reviews_enabled = models.BooleanField(default=True)
    review_moderation_enabled = models.BooleanField(default=True)
    announcements_enabled = models.BooleanField(default=True)
    email_notifications_enabled = models.BooleanField(default=True)
    notification_channels = models.JSONField(default=default_notification_channels)
    whatsapp_phone_number_id = models.CharField(max_length=80, blank=True)
    report_notifications_enabled = models.BooleanField(default=True)
    terms_text = models.TextField(default="These terms govern the use of the Tarmac student marketplace.")
    privacy_text = models.TextField(default="""Tarmac uses account information to provide student verification, marketplace, lost-and-found, messaging, and support services.

Student ID cards and graduation proofs are stored securely only while a manual verification request is pending and are accessible only to authorized Tarmac reviewers. The image is deleted from Tarmac's database and media storage when the request is approved or rejected. ID images are not retained for automatic approvals. Matriculation numbers and verification outcomes remain private account records and are never shown on public profiles.

The clear profile photo you provide is saved on your account for identity and account security. It is hidden from other students by default. You can choose to show it to verified Tarmac students in Marketplace privacy settings. Your email, phone and WhatsApp numbers, matriculation number, verification documents, private report evidence and support details are not shown to other students or the public.

Private account information is not disclosed to other students or members of the public. It is disclosed to authorized school authorities only when they request it. Authorized Tarmac administrators can access information needed to verify accounts, prevent abuse, provide support, or protect platform safety.

Marketplace listings, reviews and messages are visible to the people who use those features; authorized administrators may review them when needed for moderation, reports or support. Lost-and-found contact details and report evidence are not displayed on public listings.""")
    community_guidelines_text = models.TextField(default="Students must treat other members with respect and use Tarmac responsibly.")
    maintenance_mode = models.BooleanField(default=False)
    maintenance_message = models.CharField(max_length=240, default="Tarmac is temporarily unavailable.")
    marketplace_enabled = models.BooleanField(default=True)
    lost_found_enabled = models.BooleanField(default=True)
    jobs_enabled = models.BooleanField(default=True)
    accommodation_enabled = models.BooleanField(default=True)
    accommodation_listing_duration_days = models.PositiveSmallIntegerField(default=60)
    external_job_submissions_enabled = models.BooleanField(default=True)
    job_listing_approval_required = models.BooleanField(default=True)
    messaging_enabled = models.BooleanField(default=True)
    ads_enabled = models.BooleanField(default=True)
    ad_slide_duration_seconds = models.PositiveIntegerField(default=300)
    homepage_ads_enabled = models.BooleanField(default=True)
    marketplace_ads_enabled = models.BooleanField(default=True)
    homepage_lost_found_enabled = models.BooleanField(default=True)
    paystack_enabled = models.BooleanField(default=False)
    listing_fee_enabled = models.BooleanField(default=True)
    listing_fee_kobo = models.PositiveIntegerField(default=150000)
    sponsored_product_fee_enabled = models.BooleanField(default=True)
    sponsored_product_fee_kobo = models.PositiveIntegerField(default=50000)
    sponsored_product_duration_days = models.PositiveSmallIntegerField(default=7)
    ad_fee_enabled = models.BooleanField(default=True)
    ad_fee_kobo = models.PositiveIntegerField(default=50000)
    subscription_fee_enabled = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Platform settings"
        verbose_name_plural = "Platform settings"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)
        cache.delete("tarmac.platform_settings.v2")

    @classmethod
    def get_solo(cls):
        try:
            settings_obj = cache.get("tarmac.platform_settings.v2")
        except Exception:
            # Settings are authoritative in the database. Keep API requests
            # working when the optional shared cache is temporarily unavailable.
            logger.exception("Could not read platform settings from cache; falling back to the database.")
            settings_obj = None
        if settings_obj is None:
            from django.conf import settings as django_settings
            settings_obj = cls.objects.filter(pk=1).first() or cls(pk=1, paystack_enabled=django_settings.PAYSTACK_ENABLED_DEFAULT)
            # Admin saves explicitly invalidate this key; a longer fallback TTL
            # avoids repeatedly loading settings from the external cache/database.
            try:
                cache.set("tarmac.platform_settings.v2", settings_obj, 300)
            except Exception:
                logger.exception("Could not cache platform settings; continuing with the database value.")
        return settings_obj

    def __str__(self):
        return "Tarmac platform settings"

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.listing_fee_kobo < 0 or self.ad_fee_kobo < 0:
            raise ValidationError("Fees cannot be negative.")
        if not 1 <= self.max_product_images <= 10:
            raise ValidationError("Maximum product images must be between 1 and 10.")
        if not 5 <= self.ad_slide_duration_seconds <= 86400:
            raise ValidationError("Advertisement slide duration must be between 5 seconds and 24 hours.")
        if not 1 <= self.sponsored_product_duration_days <= 366:
            raise ValidationError("Sponsored product duration must be between 1 and 366 days.")


class EmergencyContact(models.Model):
    """Public emergency numbers curated by Tarmac administrators."""
    campus = models.CharField(max_length=120, blank=True, default="", db_index=True)
    name = models.CharField(max_length=120)
    phone_number = models.CharField(max_length=40)
    description = models.CharField(max_length=240, blank=True)
    location = models.CharField(max_length=120, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("sort_order", "name", "id")
        indexes = [models.Index(fields=("is_active", "sort_order"), name="emergency_active_order_idx")]

    def __str__(self):
        return f"{self.name}: {self.phone_number}"


class SupportTicket(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        IN_PROGRESS = "in_progress", "In progress"
        RESOLVED = "resolved", "Resolved"

    class Priority(models.TextChoices):
        NORMAL = "normal", "Normal"
        HIGH = "high", "High"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="support_tickets")
    subject = models.CharField(max_length=180)
    category = models.CharField(max_length=60)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.NORMAL)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-updated_at",)
        indexes = [models.Index(fields=("status", "updated_at"), name="support_status_updated_idx")]


class SupportMessage(models.Model):
    ticket = models.ForeignKey(SupportTicket, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    body = models.TextField(max_length=5000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at",)


class SupportAttachment(models.Model):
    message = models.ForeignKey(SupportMessage, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(upload_to="support_attachments/", storage=PrivateMediaStorage())
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at",)

class SubscriptionPlan(models.Model):
    AD_PAGES = {"homepage", "marketplace", "lost_found", "announcements", "jobs"}
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    price_kobo = models.PositiveIntegerField(default=0)
    duration_days = models.PositiveSmallIntegerField(default=30)
    placement = models.CharField(max_length=80, default="Marketplace")
    allowed_pages = models.JSONField(default=list, blank=True)
    max_pages = models.PositiveSmallIntegerField(default=1)
    allowed_placements = models.JSONField(default=list, blank=True)
    max_images = models.PositiveSmallIntegerField(default=3)
    visibility = models.CharField(max_length=60, default="Standard")
    features = models.JSONField(default=list, blank=True)
    is_popular = models.BooleanField(default=False)
    color = models.CharField(max_length=20, default="green")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta: ordering = ("price_kobo", "name")
    def clean(self):
        from django.core.exceptions import ValidationError
        if not 1 <= self.duration_days <= 366:
            raise ValidationError({"duration_days": "Duration must be between 1 and 366 days."})
        if not isinstance(self.allowed_pages, list) or not self.allowed_pages or any(not isinstance(page, str) or page not in self.AD_PAGES for page in self.allowed_pages) or len(set(self.allowed_pages)) != len(self.allowed_pages):
            raise ValidationError({"allowed_pages": "Choose one or more valid, unique advertising pages."})
        if not 1 <= self.max_pages <= len(self.allowed_pages):
            raise ValidationError({"max_pages": "Maximum pages must be between 1 and the selected page count."})

class StudentSubscription(models.Model):
    class Status(models.TextChoices): ACTIVE="active","Active"; EXPIRED="expired","Expired"; CANCELLED="cancelled","Cancelled"
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="subscriptions")
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.PROTECT, related_name="subscriptions")
    amount_kobo = models.PositiveIntegerField(default=0)
    payment_reference = models.CharField(max_length=100, unique=True, null=True, blank=True)
    ad_campaign = models.OneToOneField("ads.AdCampaign", null=True, blank=True, on_delete=models.SET_NULL, related_name="student_subscription")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)
    starts_at = models.DateTimeField(default=timezone.now)
    ends_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ("-created_at",)


class AnnouncementBookmark(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="announcement_bookmarks")
    announcement = models.ForeignKey(Announcement, on_delete=models.CASCADE, related_name="bookmarks")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("user", "announcement"), name="unique_announcement_bookmark")]

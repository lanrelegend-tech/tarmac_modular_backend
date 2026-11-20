from django.conf import settings
from django.db import models
from django.core.cache import cache
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible
from django.utils import timezone
from uuid import uuid4


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
    reason=models.CharField(max_length=100); details=models.TextField(blank=True); is_resolved=models.BooleanField(default=False); status=models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN); reviewer_note=models.TextField(blank=True); assigned_to=models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="assigned_reports"); created_at=models.DateTimeField(auto_now_add=True)


class ReportEvidence(models.Model):
    report = models.ForeignKey(UserReport, on_delete=models.CASCADE, related_name="evidence")
    image = models.ImageField(upload_to=report_evidence_upload_to, storage=PrivateMediaStorage())
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at",)

    def __str__(self):
        return f"Evidence for report #{self.report_id}"


class Announcement(models.Model):
    class Audience(models.TextChoices): ALL_VERIFIED="all_verified", "All verified students"; DEPARTMENT="department", "Department"
    title=models.CharField(max_length=160); body=models.TextField(); created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT); is_published=models.BooleanField(default=False); pinned=models.BooleanField(default=False); audience=models.CharField(max_length=20,choices=Audience.choices,default=Audience.ALL_VERIFIED); department=models.CharField(max_length=120,blank=True); published_at=models.DateTimeField(null=True,blank=True); delivery_cursor=models.PositiveBigIntegerField(default=0); delivery_completed=models.BooleanField(default=False); recipient_count=models.PositiveBigIntegerField(default=0); created_at=models.DateTimeField(auto_now_add=True)
class AdminLog(models.Model):
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.SET_NULL); action=models.CharField(max_length=120); target_type=models.CharField(max_length=80,blank=True); target_id=models.CharField(max_length=80,blank=True); metadata=models.JSONField(default=dict,blank=True); created_at=models.DateTimeField(auto_now_add=True)

class PlatformSettings(models.Model):
    site_name = models.CharField(max_length=100, default="Tarmac")
    site_logo = models.ImageField(upload_to="site/", blank=True, null=True)
    tagline = models.CharField(max_length=240, default="The marketplace built for students.")
    support_email = models.EmailField(blank=True)
    contact_email = models.EmailField(blank=True)
    registration_enabled = models.BooleanField(default=True)
    listing_approval_required = models.BooleanField(default=True)
    max_product_images = models.PositiveSmallIntegerField(default=5)
    student_verification_enabled = models.BooleanField(default=True)
    require_student_id = models.BooleanField(default=True)
    email_verification_required = models.BooleanField(default=True)
    auto_approve_students = models.BooleanField(default=False)
    featured_products_enabled = models.BooleanField(default=True)
    reviews_enabled = models.BooleanField(default=True)
    review_moderation_enabled = models.BooleanField(default=True)
    announcements_enabled = models.BooleanField(default=True)
    email_notifications_enabled = models.BooleanField(default=True)
    report_notifications_enabled = models.BooleanField(default=True)
    terms_text = models.TextField(default="These terms govern the use of the Tarmac student marketplace.")
    privacy_text = models.TextField(default="Tarmac respects student privacy and protects account information.")
    community_guidelines_text = models.TextField(default="Students must treat other members with respect and use Tarmac responsibly.")
    maintenance_mode = models.BooleanField(default=False)
    maintenance_message = models.CharField(max_length=240, default="Tarmac is temporarily unavailable.")
    marketplace_enabled = models.BooleanField(default=True)
    lost_found_enabled = models.BooleanField(default=True)
    messaging_enabled = models.BooleanField(default=True)
    ads_enabled = models.BooleanField(default=True)
    paystack_enabled = models.BooleanField(default=False)
    listing_fee_enabled = models.BooleanField(default=True)
    listing_fee_kobo = models.PositiveIntegerField(default=150000)
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
        cache.delete("tarmac.platform_settings")

    @classmethod
    def get_solo(cls):
        settings_obj = cache.get("tarmac.platform_settings")
        if settings_obj is None:
            from django.conf import settings as django_settings
            settings_obj = cls.objects.filter(pk=1).first() or cls(pk=1, paystack_enabled=django_settings.PAYSTACK_ENABLED_DEFAULT)
            cache.set("tarmac.platform_settings", settings_obj, 10)
        return settings_obj

    def __str__(self):
        return "Tarmac platform settings"

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.listing_fee_kobo < 0 or self.ad_fee_kobo < 0:
            raise ValidationError("Fees cannot be negative.")
        if not 1 <= self.max_product_images <= 10:
            raise ValidationError("Maximum product images must be between 1 and 10.")


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
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    price_kobo = models.PositiveIntegerField(default=0)
    duration_days = models.PositiveSmallIntegerField(default=30)
    placement = models.CharField(max_length=80, default="Marketplace")
    max_images = models.PositiveSmallIntegerField(default=3)
    visibility = models.CharField(max_length=60, default="Standard")
    features = models.JSONField(default=list, blank=True)
    is_popular = models.BooleanField(default=False)
    color = models.CharField(max_length=20, default="green")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta: ordering = ("price_kobo", "name")

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

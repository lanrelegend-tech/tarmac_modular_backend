from django.conf import settings
from django.db import models
from django.core.cache import cache
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible
from uuid import uuid4


def report_evidence_upload_to(instance, filename):
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else "jpg"
    return f"report_evidence/{uuid4().hex}.{extension}"


@deconstructible
class PrivateMediaStorage(FileSystemStorage):
    def __init__(self):
        super().__init__(location=settings.PRIVATE_MEDIA_ROOT)


class UserReport(models.Model):
    reporter=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,related_name="filed_reports"); reported_user=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.SET_NULL,related_name="reports_against")
    product=models.ForeignKey("products.Product",null=True,blank=True,on_delete=models.SET_NULL); lost_found_report=models.ForeignKey("lost_found.LostFoundReport",null=True,blank=True,on_delete=models.SET_NULL)
    reason=models.CharField(max_length=100); details=models.TextField(blank=True); is_resolved=models.BooleanField(default=False); created_at=models.DateTimeField(auto_now_add=True)


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
    title=models.CharField(max_length=160); body=models.TextField(); created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT); is_published=models.BooleanField(default=False); audience=models.CharField(max_length=20,choices=Audience.choices,default=Audience.ALL_VERIFIED); department=models.CharField(max_length=120,blank=True); published_at=models.DateTimeField(null=True,blank=True); delivery_cursor=models.PositiveBigIntegerField(default=0); delivery_completed=models.BooleanField(default=False); recipient_count=models.PositiveBigIntegerField(default=0); created_at=models.DateTimeField(auto_now_add=True)
class AdminLog(models.Model):
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.SET_NULL); action=models.CharField(max_length=120); target_type=models.CharField(max_length=80,blank=True); target_id=models.CharField(max_length=80,blank=True); metadata=models.JSONField(default=dict,blank=True); created_at=models.DateTimeField(auto_now_add=True)

class PlatformSettings(models.Model):
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

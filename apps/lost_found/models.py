from django.conf import settings
from django.db import models
from django.utils import timezone
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible
from uuid import uuid4

@deconstructible
class ClaimEvidenceStorage(FileSystemStorage):
    def __init__(self): super().__init__(location=settings.PRIVATE_MEDIA_ROOT)

def claim_evidence_upload_to(instance, filename):
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    return f"item_claims/{uuid4().hex}.{extension}"
class LostFoundReport(models.Model):
    public_id=models.UUIDField(default=uuid4, unique=True, editable=False, db_index=True)
    class Type(models.TextChoices): LOST="lost","Lost"; FOUND="found","Found"
    class Status(models.TextChoices): PENDING_REVIEW="pending_review","Pending review"; OPEN="open","Open"; MATCHED="matched","Potential match"; RETURNED="returned","Returned"; REJECTED="rejected","Rejected"; CLOSED="closed","Closed"
    reporter=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,related_name="lost_found_reports"); report_type=models.CharField(max_length=10,choices=Type.choices)
    campus=models.CharField(max_length=120, db_index=True, blank=True); title=models.CharField(max_length=160); description=models.TextField(); category=models.ForeignKey("products.Category",null=True,blank=True,on_delete=models.SET_NULL); category_label=models.CharField(max_length=80, blank=True); reward=models.CharField(max_length=120, blank=True); reviewer_note=models.TextField(blank=True)
    colour=models.CharField(max_length=60,blank=True); location=models.CharField(max_length=160); incident_date=models.DateField(); identifying_details=models.TextField(blank=True); contact_details=models.CharField(max_length=160, blank=True)
    image=models.ImageField(upload_to="lost_found/",blank=True,null=True); status=models.CharField(max_length=20,choices=Status.choices,default=Status.PENDING_REVIEW); is_active=models.BooleanField(default=False); matching_processed=models.BooleanField(default=False); created_at=models.DateTimeField(auto_now_add=True); deleted_at=models.DateTimeField(null=True, blank=True, db_index=True)
    class Meta:
        indexes = [models.Index(fields=("report_type", "is_active", "status"), name="lostfound_active_type_idx"), models.Index(fields=("category", "report_type", "is_active"), name="lostfound_category_type_idx"), models.Index(fields=("incident_date",), name="lostfound_date_idx")]
class ItemMatch(models.Model):
    lost_report=models.ForeignKey(LostFoundReport,on_delete=models.CASCADE,related_name="matches_as_lost"); found_report=models.ForeignKey(LostFoundReport,on_delete=models.CASCADE,related_name="matches_as_found")
    score=models.PositiveSmallIntegerField(); reasons=models.JSONField(default=list,blank=True); is_reviewed=models.BooleanField(default=False); is_dismissed=models.BooleanField(default=False); created_at=models.DateTimeField(auto_now_add=True)
    class Meta: constraints=[models.UniqueConstraint(fields=["lost_report","found_report"],name="unique_lost_found_pair")]

class ItemMatchReview(models.Model):
    class Decision(models.TextChoices): POSSIBLE_MATCH="possible_match", "Possible match"; NOT_A_MATCH="not_a_match", "Not a match"
    match = models.ForeignKey(ItemMatch, on_delete=models.CASCADE, related_name="reviews")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="lost_found_match_reviews")
    decision = models.CharField(max_length=20, choices=Decision.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=("match", "user"), name="unique_match_review_per_user")]


class ItemClaim(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    report = models.ForeignKey(LostFoundReport, on_delete=models.CASCADE, related_name="claims")
    claimant = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="item_claims")
    details = models.TextField(max_length=3000)
    evidence = models.FileField(upload_to=claim_evidence_upload_to, storage=ClaimEvidenceStorage(), null=True, blank=True)
    evidence_name = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    reviewer_note = models.CharField(max_length=1000, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="reviewed_item_claims")
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [models.UniqueConstraint(fields=("report", "claimant"), condition=models.Q(status="pending"), name="unique_pending_claimant_report")]

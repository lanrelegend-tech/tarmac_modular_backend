from django.conf import settings
from django.db import models
class LostFoundReport(models.Model):
    class Type(models.TextChoices): LOST="lost","Lost"; FOUND="found","Found"
    class Status(models.TextChoices): OPEN="open","Open"; MATCHED="matched","Potential match"; RETURNED="returned","Returned"; CLOSED="closed","Closed"
    reporter=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,related_name="lost_found_reports"); report_type=models.CharField(max_length=10,choices=Type.choices)
    title=models.CharField(max_length=160); description=models.TextField(); category=models.ForeignKey("products.Category",null=True,blank=True,on_delete=models.SET_NULL)
    colour=models.CharField(max_length=60,blank=True); location=models.CharField(max_length=160); incident_date=models.DateField(); identifying_details=models.TextField(blank=True)
    image=models.ImageField(upload_to="lost_found/",blank=True,null=True); status=models.CharField(max_length=20,choices=Status.choices,default=Status.OPEN); is_active=models.BooleanField(default=True); matching_processed=models.BooleanField(default=False); created_at=models.DateTimeField(auto_now_add=True)
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

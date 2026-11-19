from django.conf import settings
from django.db import models
class Notification(models.Model):
    class Channel(models.TextChoices): EMAIL="email","Email"; WHATSAPP="whatsapp","WhatsApp"; IN_APP="in_app","In-app"
    user=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,related_name="notifications"); channel=models.CharField(max_length=12,choices=Channel.choices)
    subject=models.CharField(max_length=180,blank=True); body=models.TextField(); data=models.JSONField(default=dict,blank=True); is_sent=models.BooleanField(default=False); is_read=models.BooleanField(default=False); created_at=models.DateTimeField(auto_now_add=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    dedupe_key = models.CharField(max_length=180, unique=True, null=True, blank=True)
    class Meta:
        indexes = [models.Index(fields=("user", "created_at"), name="notification_user_created_idx"), models.Index(fields=("is_sent", "next_attempt_at"), name="notification_delivery_due_idx")]

from django.conf import settings
from django.db import models
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible
from uuid import uuid4

@deconstructible
class PrivateChatStorage(FileSystemStorage):
    def __init__(self): super().__init__(location=settings.PRIVATE_MEDIA_ROOT)

def chat_attachment_upload_to(instance, filename):
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    return f"chat_attachments/{uuid4().hex}.{extension}"
class Conversation(models.Model):
    class Status(models.TextChoices): OPEN="open","Open"; RESOLVED="resolved","Resolved"
    class ContextType(models.TextChoices): GENERAL="general","General"; MARKETPLACE="marketplace","Marketplace"; LOST_FOUND="lost_found","Lost and found"; JOBS="jobs","Jobs and gigs"; OFFICIAL="official","Official Tarmac"; OFFICIAL_SUPPORT="official_support","Tarmac Support"; OFFICIAL_ALERT="official_alert","Official alert"; OFFICIAL_PROMO="official_promo","Official promotion"
    participants=models.ManyToManyField(settings.AUTH_USER_MODEL,related_name="conversations"); completed_by=models.ManyToManyField(settings.AUTH_USER_MODEL,related_name="completed_conversations",blank=True); completed_at=models.DateTimeField(null=True,blank=True); created_at=models.DateTimeField(auto_now_add=True); updated_at=models.DateTimeField(auto_now=True); status=models.CharField(max_length=12,choices=Status.choices,default=Status.OPEN)
    context_type=models.CharField(max_length=20,choices=ContextType.choices,default=ContextType.GENERAL); context_id=models.PositiveBigIntegerField(null=True,blank=True); context_title=models.CharField(max_length=180,blank=True); is_official=models.BooleanField(default=False)

class ConversationParticipantSnapshot(models.Model):
    conversation=models.ForeignKey(Conversation,on_delete=models.CASCADE,related_name="participant_snapshots")
    user=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,related_name="conversation_identity_snapshots")
    identity_type=models.CharField(max_length=12,choices=(("normal","Normal profile"),("anonymous","Anonymous marketplace identity")),default="normal")
    display_name=models.CharField(max_length=150)
    public_identity_id=models.CharField(max_length=36,blank=True)
    verification_badge=models.CharField(max_length=12,blank=True)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=("conversation","user"),name="unique_conversation_identity_snapshot")]

class BlockedUser(models.Model):
    blocked_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,related_name="blocked_accounts")
    blocked_user=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,related_name="blocked_by_accounts")
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=("blocked_by","blocked_user"),name="unique_user_block")]
        indexes=[models.Index(fields=("blocked_by","blocked_user"),name="user_block_lookup_idx")]
class Message(models.Model):
    conversation=models.ForeignKey(Conversation,on_delete=models.CASCADE,related_name="messages"); sender=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE)
    body=models.TextField(); created_at=models.DateTimeField(auto_now_add=True); read_at=models.DateTimeField(null=True,blank=True)

class MessageAttachment(models.Model):
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(upload_to=chat_attachment_upload_to, storage=PrivateChatStorage())
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: ordering = ("created_at",)

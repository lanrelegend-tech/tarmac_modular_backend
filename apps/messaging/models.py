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
    class ContextType(models.TextChoices): GENERAL="general","General"; MARKETPLACE="marketplace","Marketplace"; LOST_FOUND="lost_found","Lost and found"; OFFICIAL="official","Official Tarmac"
    participants=models.ManyToManyField(settings.AUTH_USER_MODEL,related_name="conversations"); completed_by=models.ManyToManyField(settings.AUTH_USER_MODEL,related_name="completed_conversations",blank=True); completed_at=models.DateTimeField(null=True,blank=True); created_at=models.DateTimeField(auto_now_add=True); updated_at=models.DateTimeField(auto_now=True); status=models.CharField(max_length=12,choices=Status.choices,default=Status.OPEN)
    context_type=models.CharField(max_length=20,choices=ContextType.choices,default=ContextType.GENERAL); context_id=models.PositiveBigIntegerField(null=True,blank=True); context_title=models.CharField(max_length=180,blank=True); is_official=models.BooleanField(default=False)
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

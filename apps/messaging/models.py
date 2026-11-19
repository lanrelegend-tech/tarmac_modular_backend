from django.conf import settings
from django.db import models
class Conversation(models.Model):
    participants=models.ManyToManyField(settings.AUTH_USER_MODEL,related_name="conversations"); created_at=models.DateTimeField(auto_now_add=True)
class Message(models.Model):
    conversation=models.ForeignKey(Conversation,on_delete=models.CASCADE,related_name="messages"); sender=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE)
    body=models.TextField(); created_at=models.DateTimeField(auto_now_add=True); read_at=models.DateTimeField(null=True,blank=True)

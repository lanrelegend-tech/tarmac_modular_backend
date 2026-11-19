from rest_framework import serializers
from .models import Notification
class NotificationSerializer(serializers.ModelSerializer):
    class Meta: model=Notification; fields=("id","channel","subject","body","data","is_sent","is_read","created_at"); read_only_fields=("channel","subject","body","data","is_sent","created_at")

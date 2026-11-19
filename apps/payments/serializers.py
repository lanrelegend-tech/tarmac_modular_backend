from rest_framework import serializers
from .models import Payment
class PaymentSerializer(serializers.ModelSerializer):
    class Meta: model=Payment; fields=("id","product","advertisement","reference","amount_kobo","currency","status","created_at","paid_at"); read_only_fields=("reference","amount_kobo","currency","status","created_at","paid_at")

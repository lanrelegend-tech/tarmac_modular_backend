from rest_framework import serializers
from .models import Payment
class PaymentSerializer(serializers.ModelSerializer):
    class Meta: model=Payment; fields=("id","product","advertisement","subscription_plan","payer_email","reference","amount_kobo","currency","status","refund_status","refund_reference","refund_requested_at","created_at","paid_at"); read_only_fields=("payer_email","reference","amount_kobo","currency","status","refund_status","refund_reference","refund_requested_at","created_at","paid_at")

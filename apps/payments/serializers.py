from rest_framework import serializers
from .models import Payment
class PaymentSerializer(serializers.ModelSerializer):
    product_title = serializers.CharField(source="product.title", read_only=True, allow_null=True)
    advertisement_title = serializers.CharField(source="advertisement.title", read_only=True, allow_null=True)
    subscription_plan_name = serializers.CharField(source="subscription_plan.name", read_only=True, allow_null=True)
    payment_type = serializers.SerializerMethodField()

    class Meta: model=Payment; fields=("id","product","product_title","advertisement","advertisement_title","subscription_plan","subscription_plan_name","payment_type","payer_email","reference","amount_kobo","currency","status","refund_status","refund_reference","refund_requested_at","created_at","paid_at"); read_only_fields=fields

    def get_payment_type(self, obj):
        if obj.advertisement_id:
            return "Advertisement"
        if obj.product_id:
            return "Marketplace listing fee"
        if obj.subscription_plan_id:
            return "Advertising package"
        return "Tarmac payment"

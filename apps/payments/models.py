import uuid
from django.conf import settings
from django.db import models
def make_payment_reference(): return uuid.uuid4().hex
class Payment(models.Model):
    class Status(models.TextChoices): INITIALIZED="initialized","Initialized"; SUCCESS="success","Success"; FAILED="failed","Failed"
    user=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name="payments"); product=models.ForeignKey("products.Product",null=True,blank=True,on_delete=models.SET_NULL,related_name="payments"); advertisement=models.ForeignKey("ads.AdCampaign",null=True,blank=True,on_delete=models.SET_NULL,related_name="payments")
    reference=models.CharField(max_length=100,unique=True,default=make_payment_reference); amount_kobo=models.PositiveIntegerField(default=150000); currency=models.CharField(max_length=3,default="NGN")
    status=models.CharField(max_length=20,choices=Status.choices,default=Status.INITIALIZED); provider_response=models.JSONField(default=dict,blank=True); created_at=models.DateTimeField(auto_now_add=True); paid_at=models.DateTimeField(null=True,blank=True)
    class Meta:
        indexes = [models.Index(fields=("status", "created_at"), name="payment_status_created_idx"), models.Index(fields=("status", "paid_at"), name="payment_status_paid_idx")]

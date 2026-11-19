from django.conf import settings
from django.db import models
from django.core.validators import MinValueValidator
class Order(models.Model):
    class Status(models.TextChoices): REQUESTED="requested","Requested"; ACCEPTED="accepted","Accepted"; COMPLETED="completed","Completed"; CANCELLED="cancelled","Cancelled"
    buyer=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name="orders_as_buyer"); seller=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name="orders_as_seller")
    product=models.ForeignKey("products.Product",on_delete=models.PROTECT,related_name="orders"); quantity=models.PositiveIntegerField(default=1); agreed_price=models.DecimalField(max_digits=12,decimal_places=2,null=True,blank=True,validators=[MinValueValidator(0.01)])
    status=models.CharField(max_length=20,choices=Status.choices,default=Status.REQUESTED); buyer_note=models.TextField(blank=True); created_at=models.DateTimeField(auto_now_add=True); updated_at=models.DateTimeField(auto_now=True)

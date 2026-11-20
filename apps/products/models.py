from django.conf import settings
from django.db import models
from django.core.validators import MinValueValidator
class Category(models.Model):
    name=models.CharField(max_length=80, unique=True); slug=models.SlugField(unique=True); is_active=models.BooleanField(default=True); is_allowed_for_sale=models.BooleanField(default=True)
    def __str__(self): return self.name
class Product(models.Model):
    class Condition(models.TextChoices): LIKE_NEW="like_new","Like new"; VERY_GOOD="very_good","Very good"; GOOD="good","Good"; FAIR="fair","Fair"
    class Status(models.TextChoices): PENDING_PAYMENT="pending_payment","Pending payment"; PENDING_REVIEW="pending_review","Pending school review"; ACTIVE="active","Active"; RESERVED="reserved","Reserved"; SOLD="sold","Sold"; EXPIRED="expired","Expired"; REMOVED="removed","Removed"
    seller=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,related_name="products"); category=models.ForeignKey(Category,on_delete=models.PROTECT,related_name="products")
    title=models.CharField(max_length=160); description=models.TextField(); condition=models.CharField(max_length=20,choices=Condition.choices)
    price=models.DecimalField(max_digits=12,decimal_places=2,validators=[MinValueValidator(0.01)]); negotiable=models.BooleanField(default=False); location=models.CharField(max_length=160)
    status=models.CharField(max_length=24,choices=Status.choices,default=Status.PENDING_PAYMENT); expires_at=models.DateTimeField(null=True,blank=True); is_featured=models.BooleanField(default=False); featured_until=models.DateTimeField(null=True,blank=True)
    sponsor_requested=models.BooleanField(default=False); sponsorship_fee_kobo=models.PositiveIntegerField(default=0); sponsorship_duration_days=models.PositiveSmallIntegerField(default=7); featured_position=models.PositiveIntegerField(null=True,blank=True)
    created_at=models.DateTimeField(auto_now_add=True); updated_at=models.DateTimeField(auto_now=True)
    class Meta:
        indexes = [models.Index(fields=("status", "created_at"), name="product_status_created_idx"), models.Index(fields=("status", "expires_at"), name="product_status_expiry_idx"), models.Index(fields=("category", "status"), name="product_category_status_idx"), models.Index(fields=("is_featured", "featured_position", "created_at"), name="product_sponsored_order_idx")]
    def __str__(self): return self.title
class ProductImage(models.Model):
    product=models.ForeignKey(Product,on_delete=models.CASCADE,related_name="images"); image=models.ImageField(upload_to="products/"); alt_text=models.CharField(max_length=160,blank=True)
class Favourite(models.Model):
    user=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,related_name="favourites"); product=models.ForeignKey(Product,on_delete=models.CASCADE,related_name="favourited_by"); created_at=models.DateTimeField(auto_now_add=True)
    class Meta: constraints=[models.UniqueConstraint(fields=["user","product"],name="unique_user_product_favourite")]

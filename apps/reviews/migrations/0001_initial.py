from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("orders", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="SellerReview",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("rating", models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])),
                ("comment", models.TextField(blank=True, max_length=1200)),
                ("status", models.CharField(choices=[("pending", "Pending moderation"), ("approved", "Visible"), ("hidden", "Hidden")], db_index=True, default="pending", max_length=12)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("order", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="seller_review", to="orders.order")),
                ("reviewer", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="seller_reviews_written", to=settings.AUTH_USER_MODEL)),
                ("seller", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="seller_reviews_received", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ("-created_at",), "indexes": [models.Index(fields=["seller", "status", "created_at"], name="seller_review_lookup_idx")]},
        ),
    ]

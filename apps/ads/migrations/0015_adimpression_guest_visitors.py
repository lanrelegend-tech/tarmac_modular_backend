import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ads", "0014_adcampaign_management_token_hash"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="adimpression",
            name="unique_daily_user_ad_impression",
        ),
        migrations.AlterField(
            model_name="adimpression",
            name="user",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="ad_impressions", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name="adimpression",
            name="visitor_hash",
            field=models.CharField(blank=True, db_index=True, max_length=64),
        ),
        migrations.AddConstraint(
            model_name="adimpression",
            constraint=models.UniqueConstraint(condition=models.Q(user__isnull=False), fields=("campaign", "user", "day"), name="unique_daily_user_ad_impression"),
        ),
        migrations.AddConstraint(
            model_name="adimpression",
            constraint=models.UniqueConstraint(condition=~models.Q(visitor_hash=""), fields=("campaign", "visitor_hash", "day"), name="unique_daily_visitor_ad_impression"),
        ),
    ]

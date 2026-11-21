import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("ads", "0012_adcampaign_advertising_policy_acceptance")]
    operations = [migrations.AddField(model_name="adcampaign", name="waitlist_entry", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="campaigns", to="ads.adwaitlistentry"))]

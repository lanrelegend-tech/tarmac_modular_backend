from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("ads", "0013_adcampaign_waitlist_entry")]
    operations = [migrations.AddField(model_name="adcampaign", name="management_token_hash", field=models.CharField(blank=True, db_index=True, max_length=64))]

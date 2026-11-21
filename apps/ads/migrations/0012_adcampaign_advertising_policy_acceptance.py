from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("ads", "0011_adcampaign_reviewer_note")]
    operations = [
        migrations.AddField(model_name="adcampaign", name="advertising_policy_accepted", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="adcampaign", name="advertising_policy_accepted_at", field=models.DateTimeField(blank=True, null=True)),
    ]

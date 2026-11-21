from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("ads", "0010_adcampaign_ad_placements_alter_adslothold_campaign_and_more")]
    operations = [migrations.AddField(model_name="adcampaign", name="reviewer_note", field=models.TextField(blank=True))]

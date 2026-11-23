from django.db import migrations, models


def populate_pricing_snapshots(apps, schema_editor):
    AdCampaign = apps.get_model("ads", "AdCampaign")
    for campaign in AdCampaign.objects.select_related("package").iterator():
        package = campaign.package
        campaign.plan_name_snapshot = package.name if package else ""
        campaign.plan_price_kobo = campaign.price_kobo or (
            package.price_kobo if package and campaign.status == "pending_payment" else 0
        )
        campaign.payment_required_snapshot = (
            campaign.price_kobo > 0 or campaign.status == "pending_payment"
        )
        campaign.save(update_fields=(
            "plan_name_snapshot", "plan_price_kobo", "payment_required_snapshot"
        ))


class Migration(migrations.Migration):
    dependencies = [("ads", "0016_two_ad_placements_per_page")]

    operations = [
        migrations.AddField(
            model_name="adcampaign",
            name="plan_name_snapshot",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="adcampaign",
            name="plan_price_kobo",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="adcampaign",
            name="payment_required_snapshot",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(populate_pricing_snapshots, migrations.RunPython.noop),
    ]

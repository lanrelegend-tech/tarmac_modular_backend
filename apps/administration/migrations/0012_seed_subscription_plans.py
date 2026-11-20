from django.db import migrations


def seed_plans(apps, schema_editor):
    Plan = apps.get_model("administration", "SubscriptionPlan")
    plans = [
        {"name": "Starter", "description": "A simple way to promote your business to students.", "price_kobo": 300000, "duration_days": 3, "placement": "Marketplace", "max_images": 3, "visibility": "Standard", "features": ["Marketplace advertisement", "3 days promotion", "Standard visibility"], "color": "blue"},
        {"name": "Growth", "description": "More reach and priority visibility across campus.", "price_kobo": 750000, "duration_days": 7, "placement": "Homepage + Marketplace", "max_images": 6, "visibility": "Priority", "features": ["Featured marketplace placement", "Priority visibility", "Sponsored badge"], "is_popular": True, "color": "green"},
        {"name": "Premium", "description": "Maximum visibility for serious promotions.", "price_kobo": 1500000, "duration_days": 14, "placement": "Homepage", "max_images": 10, "visibility": "Top priority", "features": ["Homepage promotion", "Priority positioning", "Premium sponsored badge"], "color": "purple"},
    ]
    for item in plans:
        name = item.pop("name")
        Plan.objects.get_or_create(name=name, defaults=item)


def remove_seed_plans(apps, schema_editor):
    Plan = apps.get_model("administration", "SubscriptionPlan")
    Plan.objects.filter(name__in=("Starter", "Growth", "Premium"), subscriptions__isnull=True, ad_campaigns__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [("administration", "0011_studentsubscription_ad_campaign_and_more")]
    operations = [migrations.RunPython(seed_plans, remove_seed_plans)]

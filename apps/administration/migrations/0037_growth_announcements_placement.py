from django.db import migrations


def add_growth_announcement_placement(apps, schema_editor):
    Plan = apps.get_model("administration", "SubscriptionPlan")
    Placement = apps.get_model("ads", "AdPlacement")
    database = schema_editor.connection.alias

    plan = Plan.objects.using(database).filter(name__iexact="Growth").first()
    announcement_slot = Placement.objects.using(database).filter(
        key="announcements.sponsored_card", is_active=True
    ).first()
    if not plan or not announcement_slot:
        return

    allowed_pages = list(plan.allowed_pages or [])
    if "announcements" not in allowed_pages:
        allowed_pages.append("announcements")

    allowed_placements = list(plan.allowed_placements or [])
    if announcement_slot.key not in allowed_placements:
        allowed_placements.append(announcement_slot.key)

    plan.allowed_pages = allowed_pages
    plan.allowed_placements = allowed_placements
    plan.max_pages = min(len(allowed_placements), max(plan.max_pages, len(allowed_pages)))
    plan.placement = "Homepage + Marketplace + Announcements"
    plan.features = list(plan.features or [])
    plan.features = [feature for feature in plan.features if "placement" not in feature.lower()]
    plan.features.extend([
        "Choose up to 3 placements: Homepage, Marketplace, and Announcements",
        "Sponsored post in the announcements feed",
    ])
    plan.save(update_fields=("allowed_pages", "allowed_placements", "max_pages", "placement", "features"))


class Migration(migrations.Migration):
    dependencies = [
        ("administration", "0036_announcement_category"),
        ("ads", "0018_adbookmark"),
    ]

    operations = [
        migrations.RunPython(add_growth_announcement_placement, migrations.RunPython.noop),
    ]

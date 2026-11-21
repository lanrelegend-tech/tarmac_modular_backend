from django.db import migrations


PLACEMENTS = [
    ("homepage.main_banner", "homepage", "Homepage Main Banner", 1, 1, True, 1),
    ("homepage.featured_brand", "homepage", "Homepage Featured Brand", 4, 2, False, 2),
    ("homepage.secondary_banner", "homepage", "Homepage Secondary Banner", 4, 2, False, 3),
    ("marketplace.banner", "marketplace", "Marketplace Banner", 4, 2, False, 1),
    ("marketplace.sponsored_card", "marketplace", "Sponsored Marketplace Card", 6, 3, False, 2),
    ("marketplace.featured_brand", "marketplace", "Marketplace Featured Brand", 4, 2, False, 3),
    ("announcements.banner", "announcements", "Announcements Banner", 3, 2, False, 1),
    ("announcements.sponsored_card", "announcements", "Sponsored Announcement Card", 4, 2, False, 2),
    ("announcements.featured_brand", "announcements", "Announcements Featured Brand", 4, 2, False, 3),
    ("jobs.banner", "jobs", "Jobs & Gigs Banner", 3, 2, False, 1),
    ("jobs.sponsored_card", "jobs", "Sponsored Jobs & Gigs Card", 4, 2, False, 2),
    ("jobs.featured_brand", "jobs", "Jobs & Gigs Featured Brand", 4, 2, False, 3),
    ("lost_found.banner", "lost_found", "Lost & Found Banner", 3, 2, False, 1),
    ("lost_found.sponsored_card", "lost_found", "Sponsored Lost & Found Card", 4, 2, False, 2),
    ("lost_found.featured_brand", "lost_found", "Lost & Found Featured Brand", 4, 2, False, 3),
]


def seed_placements(apps, schema_editor):
    Placement = apps.get_model("ads", "AdPlacement")
    Campaign = apps.get_model("ads", "AdCampaign")
    Plan = apps.get_model("administration", "SubscriptionPlan")
    by_key = {}
    for key, page, position, capacity, limited, exclusive, order in PLACEMENTS:
        placement, _ = Placement.objects.using(schema_editor.connection.alias).get_or_create(
            key=key,
            defaults={
                "page": page,
                "position": position,
                "description": f"{position} placement",
                "max_slots": capacity,
                "limited_slots_threshold": limited,
                "is_exclusive": exclusive,
                "sort_order": order,
                "is_active": True,
            },
        )
        by_key[key] = placement

    page_default = {
        "homepage": "homepage.main_banner",
        "marketplace": "marketplace.banner",
        "announcements": "announcements.banner",
        "jobs": "jobs.banner",
        "lost_found": "lost_found.banner",
    }
    for campaign in Campaign.objects.using(schema_editor.connection.alias).filter(ad_placement__isnull=True):
        page = campaign.placement if campaign.placement in page_default else "marketplace"
        campaign.ad_placement_id = by_key[page_default[page]].pk
        campaign.save(update_fields=("ad_placement",))

    for plan in Plan.objects.using(schema_editor.connection.alias).all():
        pages = plan.allowed_pages or ["marketplace"]
        keys = [page_default[page] for page in pages if page in page_default]
        if keys:
            plan.allowed_placements = keys
            plan.save(update_fields=("allowed_placements",))


class Migration(migrations.Migration):
    dependencies = [
        ("ads", "0008_adadvertiser_adcampaign_approved_at_and_more"),
        ("administration", "0025_subscriptionplan_allowed_placements"),
    ]

    operations = [migrations.RunPython(seed_placements, migrations.RunPython.noop)]

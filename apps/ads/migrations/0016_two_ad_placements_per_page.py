from django.db import migrations


PAGE_FALLBACKS = {
    "homepage": ("homepage.main_banner", "homepage.secondary_banner"),
    "marketplace": ("marketplace.banner", "marketplace.sponsored_card"),
    "announcements": ("announcements.banner", "announcements.sponsored_card"),
    "jobs": ("jobs.banner", "jobs.sponsored_card"),
    "lost_found": ("lost_found.banner", "lost_found.sponsored_card"),
}


def reduce_ad_placements(apps, schema_editor):
    AdPlacement = apps.get_model("ads", "AdPlacement")
    AdCampaign = apps.get_model("ads", "AdCampaign")
    AdSlotHold = apps.get_model("ads", "AdSlotHold")
    AdWaitlistEntry = apps.get_model("ads", "AdWaitlistEntry")
    SubscriptionPlan = apps.get_model("administration", "SubscriptionPlan")
    db = schema_editor.connection.alias

    placements = {item.key: item for item in AdPlacement.objects.using(db).all()}
    placements_by_id = {item.pk: item for item in placements.values()}
    retired = [item for item in placements.values() if item.key.endswith(".featured_brand")]
    retired_ids = {item.pk for item in retired}
    if not retired_ids:
        return

    # Keep existing paid/running campaigns visible by remapping any campaign
    # that only used a retired slot to the page's primary remaining position.
    for campaign in AdCampaign.objects.using(db).all().iterator():
        selected = list(campaign.ad_placements.db_manager(db).all())
        if not any(item.pk in retired_ids for item in selected) and campaign.ad_placement_id not in retired_ids:
            continue
        page = next((item.page for item in selected if item.pk in retired_ids), None)
        if page is None and campaign.ad_placement_id in retired_ids:
            page = placements_by_id[campaign.ad_placement_id].page
        if page is None:
            page = campaign.placement if campaign.placement in PAGE_FALLBACKS else "marketplace"
        fallback_keys = PAGE_FALLBACKS.get(page, PAGE_FALLBACKS["marketplace"])
        fallback_placements = [placements[key] for key in fallback_keys if key in placements]
        remaining = [item for item in selected if item.pk not in retired_ids]
        if not remaining:
            remaining = fallback_placements[:1]
        campaign.ad_placements.db_manager(db).set([item.pk for item in remaining])
        if campaign.ad_placement_id in retired_ids or not campaign.ad_placement_id:
            campaign.ad_placement_id = remaining[0].pk if remaining else None
            campaign.save(using=db, update_fields=("ad_placement",))

        # Preserve a pending-review or payment hold where possible. If this
        # campaign already has a hold on its fallback placement, drop the
        # retired duplicate to respect the unique campaign/placement key.
        fallback = remaining[0] if remaining else None
        for hold in AdSlotHold.objects.using(db).filter(campaign_id=campaign.pk, placement_id__in=retired_ids):
            existing = AdSlotHold.objects.using(db).filter(campaign_id=campaign.pk, placement_id=fallback.pk).first() if fallback else None
            if existing:
                hold.delete(using=db)
            elif fallback:
                hold.placement_id = fallback.pk
                hold.save(using=db, update_fields=("placement",))
            else:
                hold.delete(using=db)

    # Old waitlist requests for a retired position remain in history but must
    # not be offered a booking for a position that no longer exists.
    AdWaitlistEntry.objects.using(db).filter(placement_id__in=retired_ids).update(
        status="cancelled", offer_token_hash="", offer_expires_at=None
    )

    # The plan editor only offers these active placement keys going forward.
    for plan in SubscriptionPlan.objects.using(db).all().iterator():
        pages = plan.allowed_pages or ["marketplace"]
        eligible = [
            key
            for page in pages
            for key in PAGE_FALLBACKS.get(page, ())
            if key in placements
        ]
        if eligible:
            plan.allowed_placements = eligible
            plan.max_pages = min(max(plan.max_pages, 1), len(eligible))
            plan.save(using=db, update_fields=("allowed_placements", "max_pages"))

    AdPlacement.objects.using(db).filter(pk__in=retired_ids).update(is_active=False)


class Migration(migrations.Migration):
    dependencies = [
        ("ads", "0015_adimpression_guest_visitors"),
        ("administration", "0025_subscriptionplan_allowed_placements"),
    ]

    operations = [migrations.RunPython(reduce_ad_placements, migrations.RunPython.noop)]

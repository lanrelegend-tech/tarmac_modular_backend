from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.administration.models import PlatformSettings, StudentSubscription
from apps.ads.models import AdCampaign, AdPlacement, AdSlotHold
from apps.ads.services import get_slot_availability
from apps.notifications.services import notify_user
from apps.products.models import Product
from .models import Payment


def confirm_successful_payment(reference, data):
    """Verify provider fields, then idempotently record payment and fulfil it."""
    if (
        data.get("reference") != reference
        or data.get("status") != "success"
    ):
        return None, "not_successful"

    with transaction.atomic():
        payment = (
            Payment.objects.select_for_update(of=("self",))
            .select_related("user", "product", "advertisement__package", "subscription_plan")
            .filter(reference=reference)
            .first()
        )
        if not payment:
            return None, "not_found"
        if data.get("amount") != payment.amount_kobo or data.get("currency") != payment.currency:
            return payment, "mismatch"
        if payment.status == Payment.Status.SUCCESS:
            return payment, "already_confirmed"

        payment.status = Payment.Status.SUCCESS
        payment.paid_at = timezone.now()
        payment.provider_response = {
            "transaction_id": data.get("id"),
            "status": data.get("status"),
            "amount": data.get("amount"),
            "currency": data.get("currency"),
        }
        payment.save(update_fields=("status", "paid_at", "provider_response"))

        if payment.product_id and payment.product.status == Product.Status.PENDING_PAYMENT:
            platform = PlatformSettings.get_solo()
            product = payment.product
            product.status = Product.Status.PENDING_REVIEW if platform.listing_approval_required else Product.Status.ACTIVE
            update_fields = ["status", "expires_at", "updated_at"]
            if not platform.listing_approval_required:
                product.expires_at = timezone.now() + timedelta(days=30)
                if product.sponsor_requested and platform.sponsored_products_enabled:
                    product.is_featured = True
                    product.featured_until = timezone.now() + timedelta(days=product.sponsorship_duration_days)
                    update_fields.extend(("is_featured", "featured_until"))
            product.save(update_fields=update_fields)
            if product.status == Product.Status.PENDING_REVIEW:
                from apps.notifications.staff import notify_staff_section
                notify_staff_section("marketplace", "Marketplace listing needs review", f'"{product.title}" was paid for and is waiting for approval.', {"kind": "product_review", "product_id": product.pk}, f"product-review:{product.pk}")
            if payment.user_id:
                notify_user(
                    payment.user,
                    "Listing payment received",
                    f"Your listing '{product.title}' is " + ("awaiting school review." if platform.listing_approval_required else "now active."),
                    {"kind": "listing_review", "notification_category": "payments", "product_id": payment.product_id, "title": product.title},
                    f"listing-review:{payment.product_id}",
                )
        elif payment.advertisement_id and payment.advertisement.status == AdCampaign.Status.PENDING_PAYMENT:
            campaign = (
                AdCampaign.objects.select_for_update(of=("self",))
                .select_related("business_advertiser", "ad_placement")
                .prefetch_related("ad_placements")
                .get(pk=payment.advertisement_id)
            )
            placements = list(campaign.ad_placements.order_by("pk")) or ([campaign.ad_placement] if campaign.ad_placement_id else [])
            locked_placements = list(AdPlacement.objects.select_for_update().filter(pk__in=[slot.pk for slot in placements]).order_by("pk")) if placements else []
            holds = {hold.placement_id: hold for hold in AdSlotHold.objects.select_for_update().filter(campaign=campaign, expires_at__gt=timezone.now())}
            missing = [slot for slot in locked_placements if slot.pk not in holds]
            full_slots = [slot for slot in missing if get_slot_availability(slot, campaign.starts_at, campaign.ends_at, exclude_campaign_id=campaign.pk)["remaining"] <= 0]
            if not full_slots and locked_placements:
                for slot in locked_placements:
                    AdSlotHold.objects.update_or_create(
                        campaign=campaign,
                        placement=slot,
                        defaults={"starts_at": campaign.starts_at, "ends_at": campaign.ends_at, "expires_at": campaign.ends_at, "is_confirmed": True},
                    )
            campaign.status = AdCampaign.Status.PENDING_REVIEW
            campaign.save(update_fields=("status", "updated_at"))
            from apps.notifications.staff import notify_staff_section
            notify_staff_section("ads", "Advertisement needs review", f'"{campaign.title}" payment was confirmed and it is waiting for approval.', {"kind": "ad_review", "campaign_id": campaign.pk}, f"ad-review:{campaign.pk}")
            conflict_names = ", ".join(slot.position for slot in full_slots)
            if campaign.business_advertiser_id:
                message = (
                    f"Payment for your Tarmac advertisement '{campaign.title}' is confirmed. It is now awaiting school review."
                    if not full_slots
                    else f"Payment for '{campaign.title}' is confirmed, but the requested slots ({conflict_names}) need manual availability review. Tarmac will contact you shortly."
                )
                from apps.notifications.email import send_tarmac_email
                transaction.on_commit(lambda: send_tarmac_email(
                    subject="Tarmac advertisement payment confirmed",
                    text_body=message,
                    recipient=campaign.business_advertiser.email,
                    recipient_name=campaign.business_advertiser.contact_name,
                ))
            elif payment.user_id:
                notify_user(
                    payment.user,
                    "Ad payment received",
                    f"Your ad '{campaign.title}' is awaiting school review." if not full_slots else f"Your ad '{campaign.title}' is paid, but Tarmac must review the requested slot availability.",
                    {"kind": "ad_review", "notification_category": "payments", "title": campaign.title},
                    f"ad-review:{campaign.pk}",
                )
            from apps.administration.models import AdminLog
            AdminLog.objects.create(
                actor=None,
                action="ad_campaign_paid_slot_conflict" if full_slots else "ad_campaign_payment_confirmed",
                target_type="AdCampaign",
                target_id=str(campaign.pk),
                metadata={"payment_reference": payment.reference, "awaiting_review": True, "slot_conflicts": conflict_names},
            )
        elif payment.subscription_plan_id:
            subscription, created = StudentSubscription.objects.get_or_create(
                payment_reference=payment.reference,
                defaults={
                    "user": payment.user,
                    "plan": payment.subscription_plan,
                    "amount_kobo": payment.amount_kobo,
                    "ends_at": timezone.now() + timedelta(days=payment.subscription_plan.duration_days),
                },
            )
            if created and payment.user_id:
                notify_user(
                    payment.user,
                    "Subscription activated",
                    f"Your {payment.subscription_plan.name} subscription is active.",
                    {"kind": "subscription_activated", "subscription_id": subscription.pk},
                    f"subscription-activated:{subscription.pk}",
                )
        return payment, "confirmed"

import hashlib
import hmac
import json
import os
import time
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from apps.products.models import Product
from apps.ads.models import AdCampaign
from apps.administration.models import StudentSubscription, PlatformSettings
from apps.notifications.services import notify_user
from .models import Payment


def _valid_paystack_signature(raw_body, headers):
    signing_secret = os.getenv("PAYSTACK_WEBHOOK_SECRET", "")
    signature_header = headers.get("Paystack-Signature", "")
    if signing_secret and signature_header:
        parts = {}
        signatures = []
        for piece in signature_header.split(","):
            key, separator, value = piece.strip().partition("=")
            if separator and key == "t":
                parts["timestamp"] = value
            elif separator and key == "v1":
                _key_id, colon, signature = value.partition(":")
                if colon:
                    signatures.append(signature)
        try:
            timestamp = int(parts["timestamp"])
        except (KeyError, ValueError):
            return False
        if abs(int(time.time()) - timestamp) > 300:
            return False
        signed_payload = str(timestamp).encode() + b"." + raw_body
        expected = hmac.new(signing_secret.encode(), signed_payload, hashlib.sha512).hexdigest()
        return any(hmac.compare_digest(expected, signature) for signature in signatures)

    legacy_secret = os.getenv("PAYSTACK_SECRET_KEY", "")
    legacy_signature = headers.get("x-paystack-signature", "")
    if not legacy_secret or not legacy_signature:
        return False
    expected = hmac.new(legacy_secret.encode(), raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, legacy_signature)


@api_view(["POST"])
@permission_classes([permissions.AllowAny])
def paystack_webhook(request):
    raw_body = request.body
    if not _valid_paystack_signature(raw_body, request.headers):
        return Response({"detail": "Invalid signature."}, status=status.HTTP_401_UNAUTHORIZED)
    try:
        event = json.loads(raw_body)
    except (TypeError, ValueError):
        return Response({"detail": "Invalid JSON."}, status=status.HTTP_400_BAD_REQUEST)
    event_name = event.get("event")
    if event_name and event_name.startswith("refund."):
        data = event.get("data") or {}
        transaction_reference = data.get("transaction_reference") or (data.get("transaction") or {}).get("reference")
        if not transaction_reference:
            return Response({"received": True})
        status_map = {
            "refund.pending": Payment.RefundStatus.PENDING,
            "refund.processing": Payment.RefundStatus.PROCESSING,
            "refund.needs-attention": Payment.RefundStatus.NEEDS_ATTENTION,
            "refund.failed": Payment.RefundStatus.FAILED,
            "refund.processed": Payment.RefundStatus.PROCESSED,
        }
        with transaction.atomic():
            payment = Payment.objects.select_for_update().select_related("advertisement__business_advertiser", "user").filter(reference=transaction_reference, advertisement__isnull=False).first()
            if not payment:
                return Response({"received": True})
            payment.refund_status = status_map[event_name]
            payment.refund_response = {"status": data.get("status"), "refund_reference": data.get("refund_reference"), "amount": data.get("amount"), "currency": data.get("currency"), "event": event_name}
            if data.get("refund_reference"):
                payment.refund_reference = str(data["refund_reference"])[:100]
            payment.save(update_fields=("refund_status", "refund_reference", "refund_response"))
            if event_name in ("refund.needs-attention", "refund.failed", "refund.processed"):
                message = {
                    "refund.needs-attention": "Paystack needs more information to complete your advertising refund. Please contact Tarmac Support.",
                    "refund.failed": "Paystack could not complete your advertising refund. Contact Tarmac Support so we can help resolve it.",
                    "refund.processed": "Paystack processed your advertising refund. Your bank may take several business days to show the funds.",
                }[event_name]
                campaign = payment.advertisement
                if campaign and campaign.business_advertiser_id:
                    from apps.notifications.email import send_tarmac_email
                    recipient = campaign.business_advertiser.email
                    name = campaign.business_advertiser.contact_name
                    transaction.on_commit(lambda: send_tarmac_email(subject="Tarmac advertising refund update", text_body=message, recipient=recipient, recipient_name=name))
                elif payment.user_id:
                    notify_user(payment.user, "Tarmac advertising refund update", message, {"kind": "ad_refund_update", "campaign_id": payment.advertisement_id, "refund_status": payment.refund_status}, f"ad-refund:{payment.pk}:{payment.refund_status}")
        return Response({"received": True})
    if event_name not in ("charge.success", "charge.failed"):
        return Response({"received": True})
    data = event.get("data") or {}
    reference = data.get("reference")
    if not reference:
        return Response({"received": True})
    with transaction.atomic():
        try:
            payment = Payment.objects.select_for_update().select_related("product", "advertisement__package", "subscription_plan").get(reference=reference)
        except Payment.DoesNotExist:
            return Response({"received": True})
        if payment.status == Payment.Status.SUCCESS:
            return Response({"received": True})
        if event.get("event") == "charge.failed":
            payment.status = Payment.Status.FAILED
            payment.provider_response = {"status": data.get("status"), "gateway_response": data.get("gateway_response")}
            payment.save(update_fields=("status", "provider_response"))
            return Response({"received": True})
        if data.get("status") != "success" or data.get("amount") != payment.amount_kobo or data.get("currency") != payment.currency:
            return Response({"detail": "Payment details do not match."}, status=status.HTTP_400_BAD_REQUEST)
        payment.status = Payment.Status.SUCCESS
        payment.paid_at = timezone.now()
        payment.provider_response = {"transaction_id": data.get("id"), "status": data.get("status"), "amount": data.get("amount"), "currency": data.get("currency")}
        payment.save(update_fields=("status", "paid_at", "provider_response"))
        if payment.product_id and payment.product.status == Product.Status.PENDING_PAYMENT:
            platform = PlatformSettings.get_solo()
            payment.product.status = Product.Status.PENDING_REVIEW if platform.listing_approval_required else Product.Status.ACTIVE
            update_fields = ["status", "expires_at", "updated_at"]
            if not platform.listing_approval_required:
                payment.product.expires_at = timezone.now() + timedelta(days=30)
                if payment.product.sponsor_requested and platform.sponsored_products_enabled:
                    payment.product.is_featured = True
                    payment.product.featured_until = timezone.now() + timedelta(days=payment.product.sponsorship_duration_days)
                    update_fields.extend(("is_featured", "featured_until"))
            payment.product.save(update_fields=update_fields)
            notify_user(payment.user, "Listing payment received", f"Your listing '{payment.product.title}' is " + ("awaiting school review." if platform.listing_approval_required else "now active."),
                        {"kind": "listing_review", "notification_category": "payments", "product_id": payment.product_id, "title": payment.product.title}, f"listing-review:{payment.product_id}")
        elif payment.advertisement_id and payment.advertisement.status == AdCampaign.Status.PENDING_PAYMENT:
            campaign = AdCampaign.objects.select_for_update(of=("self",)).select_related("business_advertiser", "ad_placement").prefetch_related("ad_placements").get(pk=payment.advertisement_id)
            from apps.ads.models import AdPlacement, AdSlotHold
            from apps.ads.services import get_slot_availability
            placements = list(campaign.ad_placements.order_by("pk")) or ([campaign.ad_placement] if campaign.ad_placement_id else [])
            locked_placements = list(AdPlacement.objects.select_for_update().filter(pk__in=[slot.pk for slot in placements]).order_by("pk")) if placements else []
            holds = {hold.placement_id: hold for hold in AdSlotHold.objects.select_for_update().filter(campaign=campaign, expires_at__gt=timezone.now())}
            missing = [slot for slot in locked_placements if slot.pk not in holds]
            full_slots = [slot for slot in missing if get_slot_availability(slot, campaign.starts_at, campaign.ends_at, exclude_campaign_id=campaign.pk)["remaining"] <= 0]
            if not full_slots and locked_placements:
                for slot in locked_placements:
                    AdSlotHold.objects.update_or_create(campaign=campaign, placement=slot, defaults={"starts_at": campaign.starts_at, "ends_at": campaign.ends_at, "expires_at": campaign.ends_at, "is_confirmed": True})
            campaign.status = AdCampaign.Status.PENDING_REVIEW
            campaign.save(update_fields=("status", "updated_at"))
            conflict_names = ", ".join(slot.position for slot in full_slots)
            if campaign.business_advertiser_id:
                message = f"Payment for your Tarmac advertisement '{campaign.title}' is confirmed. It is now awaiting school review." if not full_slots else f"Payment for '{campaign.title}' is confirmed, but the requested slots ({conflict_names}) need manual availability review. Tarmac will contact you shortly."
                from apps.notifications.email import send_tarmac_email
                transaction.on_commit(lambda: send_tarmac_email(subject="Tarmac advertisement payment confirmed", text_body=f"{message}\n\nUse the private campaign tracking link in your original Tarmac submission email to check the review status.", recipient=campaign.business_advertiser.email, recipient_name=campaign.business_advertiser.contact_name))
            elif payment.user_id:
                notify_user(payment.user, "Ad payment received", f"Your ad '{campaign.title}' is awaiting school review." if not full_slots else f"Your ad '{campaign.title}' is paid, but Tarmac must review the requested slot availability.",
                            {"kind": "ad_review", "notification_category": "payments", "title": campaign.title}, f"ad-review:{campaign.pk}")
            from apps.administration.models import AdminLog
            AdminLog.objects.create(actor=None, action="ad_campaign_paid_slot_conflict" if full_slots else "ad_campaign_payment_confirmed", target_type="AdCampaign", target_id=str(campaign.pk), metadata={"payment_reference": payment.reference, "awaiting_review": True, "slot_conflicts": conflict_names})
        elif payment.subscription_plan_id:
            subscription, created = StudentSubscription.objects.get_or_create(payment_reference=payment.reference, defaults={"user": payment.user, "plan": payment.subscription_plan, "amount_kobo": payment.amount_kobo, "ends_at": timezone.now() + timedelta(days=payment.subscription_plan.duration_days)})
            if created:
                notify_user(payment.user, "Subscription activated", f"Your {payment.subscription_plan.name} subscription is active.", {"kind":"subscription_activated", "subscription_id":subscription.pk}, f"subscription-activated:{subscription.pk}")
    return Response({"received": True})

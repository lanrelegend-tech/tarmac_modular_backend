import hashlib
import hmac
import json
import os
import time
from django.db import transaction
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from apps.administration.models import StudentSubscription
from apps.ads.models import AdCampaign, AdSlotHold
from apps.products.models import Product
from apps.notifications.services import notify_user
from .models import Payment
from .settlement import confirm_successful_payment


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
        if event_name not in status_map:
            return Response({"received": True})
        with transaction.atomic():
            payment = Payment.objects.select_for_update(of=("self",)).select_related("advertisement__business_advertiser", "product", "user").filter(reference=transaction_reference).first()
            if not payment:
                return Response({"received": True})
            payment.refund_status = status_map[event_name]
            payment.refund_response = {"status": data.get("status"), "refund_reference": data.get("refund_reference"), "amount": data.get("amount"), "currency": data.get("currency"), "event": event_name}
            if data.get("refund_reference"):
                payment.refund_reference = str(data["refund_reference"])[:100]
            payment.save(update_fields=("refund_status", "refund_reference", "refund_response"))
            if event_name == "refund.processed":
                if payment.product_id:
                    product = payment.product
                    product.status = Product.Status.REMOVED
                    product.is_featured = False
                    product.featured_until = None
                    product.save(update_fields=("status", "is_featured", "featured_until", "updated_at"))
                if payment.advertisement_id:
                    campaign = payment.advertisement
                    if campaign.status not in (AdCampaign.Status.REJECTED, AdCampaign.Status.ENDED, AdCampaign.Status.CANCELLED):
                        campaign.status = AdCampaign.Status.CANCELLED
                        campaign.save(update_fields=("status", "updated_at"))
                    AdSlotHold.objects.filter(campaign=campaign).delete()
                if payment.subscription_plan_id:
                    StudentSubscription.objects.filter(payment_reference=payment.reference, status=StudentSubscription.Status.ACTIVE).update(status=StudentSubscription.Status.CANCELLED, ends_at=timezone.now())
            if event_name in ("refund.needs-attention", "refund.failed", "refund.processed"):
                section = "advertisement" if payment.advertisement_id else "marketplace listing" if payment.product_id else "subscription" if payment.subscription_plan_id else "Tarmac"
                message = {
                    "refund.needs-attention": f"Paystack needs more information to complete your {section} refund. Please contact Tarmac Support.",
                    "refund.failed": f"Paystack could not complete your {section} refund. Contact Tarmac Support so we can help resolve it.",
                    "refund.processed": f"Paystack processed your {section} refund. Your bank may take several business days to show the funds.",
                }[event_name]
                campaign = payment.advertisement
                if campaign and campaign.business_advertiser_id:
                    from apps.notifications.email import send_tarmac_email
                    recipient = campaign.business_advertiser.email
                    name = campaign.business_advertiser.contact_name
                    transaction.on_commit(lambda: send_tarmac_email(subject="Tarmac refund update", text_body=message, recipient=recipient, recipient_name=name))
                elif payment.user_id:
                    notify_user(payment.user, "Tarmac refund update", message, {"kind": "payment_refund_update", "payment_id": payment.pk, "refund_status": payment.refund_status}, f"payment-refund:{payment.pk}:{payment.refund_status}")
        return Response({"received": True})
    if event_name not in ("charge.success", "charge.failed"):
        return Response({"received": True})
    data = event.get("data") or {}
    reference = data.get("reference")
    if not reference:
        return Response({"received": True})
    if event_name == "charge.failed":
        with transaction.atomic():
            payment = Payment.objects.select_for_update().filter(reference=reference).first()
            if payment and payment.status != Payment.Status.SUCCESS:
                payment.status = Payment.Status.FAILED
                payment.provider_response = {"status": data.get("status"), "gateway_response": data.get("gateway_response")}
                payment.save(update_fields=("status", "provider_response"))
        return Response({"received": True})

    payment, result = confirm_successful_payment(reference, data)
    if result == "mismatch":
        return Response({"detail": "Payment details do not match."}, status=status.HTTP_400_BAD_REQUEST)
    return Response({"received": True})

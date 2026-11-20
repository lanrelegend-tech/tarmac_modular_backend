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
    if event.get("event") not in ("charge.success", "charge.failed"):
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
                        {"kind": "listing_review", "product_id": payment.product_id, "title": payment.product.title}, f"listing-review:{payment.product_id}")
        elif payment.advertisement_id and payment.advertisement.status == AdCampaign.Status.PENDING_PAYMENT:
            payment.advertisement.status = AdCampaign.Status.PENDING_REVIEW
            payment.advertisement.save(update_fields=("status", "updated_at"))
            notify_user(payment.user, "Ad payment received", f"Your ad '{payment.advertisement.title}' is awaiting school review.",
                        {"kind": "ad_review", "title": payment.advertisement.title}, f"ad-review:{payment.advertisement_id}")
        elif payment.subscription_plan_id:
            subscription, created = StudentSubscription.objects.get_or_create(payment_reference=payment.reference, defaults={"user": payment.user, "plan": payment.subscription_plan, "amount_kobo": payment.amount_kobo, "ends_at": timezone.now() + timedelta(days=payment.subscription_plan.duration_days)})
            if created:
                notify_user(payment.user, "Subscription activated", f"Your {payment.subscription_plan.name} subscription is active.", {"kind":"subscription_activated", "subscription_id":subscription.pk}, f"subscription-activated:{subscription.pk}")
    return Response({"received": True})

import logging
import os
import uuid

import requests
from django.utils import timezone

from .models import Payment

logger = logging.getLogger(__name__)


def request_paystack_refund(payment, *, reason, actor=None):
    """Request a full Paystack refund once; final settlement arrives by webhook."""
    if payment.status != Payment.Status.SUCCESS:
        return {"status": "not_paid", "detail": "No successful payment exists to refund."}
    if payment.refund_status in (Payment.RefundStatus.PENDING, Payment.RefundStatus.PROCESSING, Payment.RefundStatus.NEEDS_ATTENTION, Payment.RefundStatus.PROCESSED):
        return {"status": payment.refund_status, "detail": "A refund request already exists for this payment."}
    secret = os.getenv("PAYSTACK_SECRET_KEY", "").strip()
    if not secret:
        # No request reached Paystack, so a staff member can safely retry after
        # configuring the key. NEEDS_ATTENTION is reserved for Paystack's webhook.
        payment.refund_status = Payment.RefundStatus.FAILED
        payment.refund_response = {"error": "PAYSTACK_SECRET_KEY is not configured."}
        payment.refund_requested_at = timezone.now()
        payment.save(update_fields=("refund_status", "refund_response", "refund_requested_at"))
        return {"status": payment.refund_status, "detail": "Refund could not be submitted because Paystack is not configured."}

    reference = f"TARAD-{payment.pk}-{uuid.uuid4().hex[:16]}"
    payment.refund_status = Payment.RefundStatus.PENDING
    payment.refund_reference = reference
    payment.refund_requested_at = timezone.now()
    payment.refund_response = {"detail": "Refund request submitted to Paystack."}
    payment.save(update_fields=("refund_status", "refund_reference", "refund_requested_at", "refund_response"))
    try:
        response = requests.post(
            "https://api.paystack.co/refund",
            json={
                "transaction": payment.reference,
                "amount": payment.amount_kobo,
                "currency": payment.currency,
                "customer_note": "Your Tarmac advertising campaign was not approved.",
                "merchant_note": f"Campaign rejection: {reason[:200]}",
                "reference": reference,
            },
            headers={"Authorization": f"Bearer {secret}", "Content-Type": "application/json"},
            timeout=20,
        )
        payload = response.json()
        if not response.ok or not payload.get("status"):
            payment.refund_status = Payment.RefundStatus.FAILED
            payment.refund_response = {"http_status": response.status_code, "message": str(payload.get("message", "Paystack rejected the refund request."))[:500]}
            payment.save(update_fields=("refund_status", "refund_response"))
            return {"status": payment.refund_status, "detail": payment.refund_response["message"]}
        refund = payload.get("data") or {}
        provider_status = refund.get("status", "pending")
        status_map = {"pending": Payment.RefundStatus.PENDING, "processing": Payment.RefundStatus.PROCESSING, "needs-attention": Payment.RefundStatus.NEEDS_ATTENTION, "needs_attention": Payment.RefundStatus.NEEDS_ATTENTION, "failed": Payment.RefundStatus.FAILED, "processed": Payment.RefundStatus.PROCESSED}
        payment.refund_status = status_map.get(provider_status, Payment.RefundStatus.PENDING)
        payment.refund_response = {"id": refund.get("id"), "status": provider_status, "amount": refund.get("amount"), "currency": refund.get("currency"), "message": str(payload.get("message", "Refund submitted."))[:300]}
        payment.save(update_fields=("refund_status", "refund_response"))
        return {"status": payment.refund_status, "detail": "Paystack accepted the refund request. The bank may take several business days to return the funds."}
    except (requests.RequestException, ValueError) as exc:
        logger.exception("Paystack ad refund request failed for payment %s", payment.pk)
        # The request may have reached Paystack before the connection failed.
        # Keep it pending so staff reconcile it by reference instead of risking a duplicate refund.
        payment.refund_status = Payment.RefundStatus.PENDING
        payment.refund_response = {"error": str(exc)[:300], "reconcile_by_reference": reference}
        payment.save(update_fields=("refund_status", "refund_response"))
        return {"status": payment.refund_status, "detail": "Paystack's response was not received. Check the refund reference in Paystack before trying again."}

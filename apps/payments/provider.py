import os
from urllib.parse import quote

import requests
from rest_framework.exceptions import APIException


class PaystackVerificationError(APIException):
    status_code = 502
    default_code = "paystack_verification_failed"


def verify_paystack_transaction(reference):
    secret = os.getenv("PAYSTACK_SECRET_KEY", "").strip()
    if not secret:
        raise PaystackVerificationError("Paystack is not configured on the server.")
    try:
        response = requests.get(
            f"https://api.paystack.co/transaction/verify/{quote(reference, safe='')}",
            headers={"Authorization": f"Bearer {secret}"},
            timeout=15,
        )
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise PaystackVerificationError("Could not verify the payment with Paystack right now.") from exc
    if not response.ok or not payload.get("status") or not isinstance(payload.get("data"), dict):
        raise PaystackVerificationError(str(payload.get("message") or "Paystack could not verify this payment.")[:300])
    return payload["data"]

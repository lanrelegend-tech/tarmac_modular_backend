"""Validation for browser push endpoints and public subscription keys."""

import base64
import binascii
import re
from urllib.parse import urlsplit


ALLOWED_PUSH_HOSTS = (
    "fcm.googleapis.com",
    "push.services.mozilla.com",
    "push.apple.com",
    "notify.windows.com",
)


def validate_push_subscription(endpoint, p256dh, auth):
    if not isinstance(endpoint, str) or len(endpoint) > 2000:
        return False
    try:
        parsed = urlsplit(endpoint)
        host = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except ValueError:
        return False
    if parsed.scheme != "https" or not host or parsed.username or parsed.password:
        return False
    if port not in (None, 443) or parsed.fragment:
        return False
    if not any(host == allowed or host.endswith(f".{allowed}") for allowed in ALLOWED_PUSH_HOSTS):
        return False
    if not isinstance(p256dh, str) or not isinstance(auth, str):
        return False
    if not re.fullmatch(r"[A-Za-z0-9_-]+", p256dh) or not re.fullmatch(r"[A-Za-z0-9_-]+", auth):
        return False
    try:
        public_key = base64.b64decode(p256dh + "=" * (-len(p256dh) % 4), altchars=b"-_", validate=True)
        auth_secret = base64.b64decode(auth + "=" * (-len(auth) % 4), altchars=b"-_", validate=True)
    except (ValueError, binascii.Error):
        return False
    return len(public_key) == 65 and len(auth_secret) == 16

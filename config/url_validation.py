"""Validation for URLs supplied by users and shown as external links."""

from ipaddress import ip_address
import re
from urllib.parse import urlsplit


def is_safe_external_url(value, *, require_https=True):
    try:
        parsed = urlsplit(value)
        hostname = (parsed.hostname or "").encode("idna").decode("ascii").lower().rstrip(".")
        parsed.port  # Trigger validation of malformed ports.
    except (UnicodeError, ValueError):
        return False

    if not hostname or parsed.username or parsed.password:
        return False
    allowed_schemes = ("https",) if require_https else ("https", "http")
    if parsed.scheme.lower() not in allowed_schemes:
        return False
    if hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal", ".test")):
        return False
    try:
        if not ip_address(hostname).is_global:
            return False
    except ValueError:
        pass
    return True


def normalize_public_contact_url(value, *, require_https=True):
    """Return a safe HTTPS destination, converting phone numbers to WhatsApp."""
    value = str(value or "").strip()
    if not value:
        return ""

    if re.fullmatch(r"[+\d\s().-]+", value):
        digits = re.sub(r"\D", "", value)
        if not 7 <= len(digits) <= 15:
            raise ValueError("Enter a valid phone number or public HTTPS link.")
        # Most Tarmac users enter Nigerian local numbers. wa.me expects the
        # international country code without a leading plus sign.
        if len(digits) == 11 and digits.startswith("0"):
            digits = f"234{digits[1:]}"
        elif len(digits) == 10:
            digits = f"234{digits}"
        return f"https://wa.me/{digits}"

    if not is_safe_external_url(value, require_https=require_https):
        raise ValueError("Use a public HTTPS website, social profile, or WhatsApp phone number.")
    return value

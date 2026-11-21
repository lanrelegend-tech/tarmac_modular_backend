import os

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
import requests

def send_tarmac_email(*, subject, text_body, recipient, recipient_name="", action_url="", action_label="Open Tarmac", preheader=""):
    """Send a multipart email using the shared, mobile-friendly Tarmac design."""
    support_email = os.getenv("SUPPORT_EMAIL", "").strip()
    if not support_email:
        try:
            from apps.administration.models import PlatformSettings

            support_email = PlatformSettings.get_solo().support_email
        except Exception:
            # Email delivery should still work during initial setup/migrations.
            support_email = ""
    html_body = render_to_string("notifications/email/base.html", {
        "subject": subject,
        "preheader": preheader or subject,
        "recipient_name": recipient_name or "there",
        "body_text": text_body,
        "action_url": action_url,
        "action_label": action_label,
        "support_email": support_email,
        "frontend_origin": os.getenv("FRONTEND_ORIGIN", ""),
    })
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    if not api_key and settings.DEBUG:
        # Keep local development usable without a Resend account.
        message = EmailMultiAlternatives(
            subject,
            text_body,
            settings.DEFAULT_FROM_EMAIL,
            [recipient],
            reply_to=[support_email] if support_email else None,
        )
        message.attach_alternative(html_body, "text/html")
        return message.send(fail_silently=False)
    if not api_key:
        raise ImproperlyConfigured("Set RESEND_API_KEY to send production email.")

    payload = {
        "from": settings.DEFAULT_FROM_EMAIL,
        "to": [recipient],
        "subject": subject,
        "html": html_body,
        "text": text_body,
    }
    if support_email:
        payload["reply_to"] = [support_email]
    response = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=15,
    )
    if not response.ok:
        raise RuntimeError(f"Resend email request failed ({response.status_code}): {response.text[:1000]}")
    return 1

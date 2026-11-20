import os

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string


def send_tarmac_email(*, subject, text_body, recipient, recipient_name="", action_url="", action_label="Open Tarmac", preheader=""):
    """Send a multipart email using the shared, mobile-friendly Tarmac design."""
    html_body = render_to_string("notifications/email/base.html", {
        "subject": subject,
        "preheader": preheader or subject,
        "recipient_name": recipient_name or "there",
        "body_text": text_body,
        "action_url": action_url,
        "action_label": action_label,
        "support_email": os.getenv("SUPPORT_EMAIL", ""),
        "frontend_origin": os.getenv("FRONTEND_ORIGIN", ""),
    })
    message = EmailMultiAlternatives(subject, text_body, settings.DEFAULT_FROM_EMAIL, [recipient])
    message.attach_alternative(html_body, "text/html")
    return message.send(fail_silently=False)

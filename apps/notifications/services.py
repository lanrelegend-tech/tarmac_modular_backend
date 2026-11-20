import logging
import os
from datetime import timedelta

import requests
from django.db import transaction
from django.utils import timezone

from .models import Notification
from .email import send_tarmac_email

logger = logging.getLogger(__name__)
RETRY_MINUTES = (1, 5, 15, 60, 360)


def queue_notification(user, channel, subject, body, data, dedupe_key):
    notification, created = Notification.objects.get_or_create(
        dedupe_key=dedupe_key,
        defaults={
            "user": user,
            "channel": channel,
            "subject": subject,
            "body": body,
            "data": data,
            "is_sent": channel == Notification.Channel.IN_APP,
        },
    )
    # Provider calls are drained by retry_notifications, keeping user-facing
    # API requests independent from SMTP/WhatsApp network latency.
    return notification


def notify_possible_match(match):
    if match.lost_report.reporter_id == match.found_report.reporter_id:
        return
    pairs = (
        (match.lost_report.reporter, match.lost_report.title),
        (match.found_report.reporter, match.found_report.title),
    )
    for user, their_title in pairs:
        notify_user(
            user,
            "Possible lost-and-found match",
            f'A possible match was found for "{their_title}". Review it in the Tarmac lost-and-found section.',
            {"kind": "lost_found_match", "match_id": match.pk, "report_title": their_title},
            f"lost-found-match:{match.pk}:{user.pk}",
        )


def notify_user(user, subject, body, data, event_key):
    queue_notification(user, Notification.Channel.IN_APP, subject, body, data, f"{event_key}:in_app")
    from apps.administration.models import PlatformSettings
    platform = PlatformSettings.get_solo()
    if platform.email_notifications_enabled and user.notification_email_enabled and user.email:
        queue_notification(user, Notification.Channel.EMAIL, subject, body, data, f"{event_key}:email")
    if user.notification_whatsapp_enabled and user.whatsapp_consent_at and user.whatsapp_number:
        template_key = {"lost_found_match": "WHATSAPP_MATCH_TEMPLATE", "order_update": "WHATSAPP_ORDER_TEMPLATE", "listing_active": "WHATSAPP_LISTING_TEMPLATE", "listing_review": "WHATSAPP_LISTING_TEMPLATE", "ad_review": "WHATSAPP_AD_TEMPLATE", "ad_approved": "WHATSAPP_AD_TEMPLATE"}.get(data.get("kind"))
        if template_key and os.getenv(template_key):
            queue_notification(user, Notification.Channel.WHATSAPP, subject, body, data, f"{event_key}:whatsapp")


def _send_email(notification):
    user = notification.user
    data = notification.data or {}
    kind = data.get("kind", "")
    origin = os.getenv("FRONTEND_ORIGIN", "http://localhost:3000").rstrip("/")
    action_url = f"{origin}/dashboard"
    action_label = "Open Tarmac"
    if kind.startswith("lost_found"):
        action_url, action_label = f"{origin}/lost-found", "Review possible match"
    elif kind in ("order_update", "order_created"):
        product_id = data.get("product_id")
        action_url = f"{origin}/marketplace/{product_id}" if product_id else f"{origin}/dashboard"
        action_label = "View order update"
    elif kind.startswith("profile_change"):
        action_url = f"{origin}/admin/profile-changes" if user.is_staff else f"{origin}/profile"
        action_label = "Review profile request" if user.is_staff else "Open profile"
    elif kind == "admin_conversation_reply":
        conversation_id = data.get("conversation_id")
        action_url = f"{origin}/messages?conversation={conversation_id}" if conversation_id else f"{origin}/messages"
        action_label = "Open official message"
    elif kind in ("conversation_completed", "conversation_completion_requested"):
        conversation_id = data.get("conversation_id")
        action_url = f"{origin}/messages?conversation={conversation_id}" if conversation_id else f"{origin}/messages"
        action_label = "Open Tarmac conversation"
    elif kind == "verification_review":
        action_url, action_label = f"{origin}/verify-student", "View verification status"
    elif kind in ("item_claim", "item_claim_review"):
        action_url, action_label = f"{origin}/lost-found", "Review item claim"
    elif kind.startswith("support"):
        action_url, action_label = f"{origin}/support", "Open support inbox"
    return send_tarmac_email(
        subject=notification.subject or "Tarmac notification",
        text_body=notification.body,
        recipient=user.email,
        recipient_name=user.first_name or user.username,
        action_url=action_url,
        action_label=action_label,
        preheader=notification.body[:140],
    )


def _send_whatsapp(notification):
    user = notification.user
    if not (user.notification_whatsapp_enabled and user.whatsapp_consent_at and user.whatsapp_number):
        return True
    token = os.getenv("WHATSAPP_ACCESS_TOKEN")
    phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
    template_key = {"lost_found_match": "WHATSAPP_MATCH_TEMPLATE", "order_update": "WHATSAPP_ORDER_TEMPLATE", "listing_active": "WHATSAPP_LISTING_TEMPLATE", "listing_review": "WHATSAPP_LISTING_TEMPLATE", "ad_review": "WHATSAPP_AD_TEMPLATE", "ad_approved": "WHATSAPP_AD_TEMPLATE"}.get(notification.data.get("kind"))
    template = os.getenv(template_key, "") if template_key else ""
    if not all((token, phone_number_id, template)):
        raise RuntimeError("Configure WhatsApp credentials and an approved match template.")
    version = os.getenv("WHATSAPP_API_VERSION", "v27.0")
    payload = {
        "messaging_product": "whatsapp",
        "to": user.whatsapp_number.lstrip("+"),
        "type": "template",
        "template": {
            "name": template,
            "language": {"code": os.getenv("WHATSAPP_TEMPLATE_LANGUAGE", "en_US")},
            "components": [{"type": "body", "parameters": [{"type": "text", "text": notification.data.get("template_parameter") or notification.data.get("report_title") or notification.data.get("title") or "an update"}]}],
        },
    }
    response = requests.post(
        f"https://graph.facebook.com/{version}/{phone_number_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
        timeout=10,
    )
    response.raise_for_status()


def dispatch_notification(notification_id):
    with transaction.atomic():
        try:
            notification = Notification.objects.select_for_update().select_related("user").get(pk=notification_id)
        except Notification.DoesNotExist:
            return
        if notification.is_sent or notification.attempts >= len(RETRY_MINUTES):
            return
        notification.attempts += 1
        try:
            if notification.channel == Notification.Channel.EMAIL:
                sent = _send_email(notification)
                if sent != 1:
                    raise RuntimeError("Email provider did not accept the message.")
            elif notification.channel == Notification.Channel.WHATSAPP:
                _send_whatsapp(notification)
            notification.is_sent = True
            notification.last_error = ""
            notification.next_attempt_at = None
        except Exception as exc:
            notification.last_error = str(exc)[:2000]
            if notification.attempts < len(RETRY_MINUTES):
                notification.next_attempt_at = timezone.now() + timedelta(minutes=RETRY_MINUTES[notification.attempts - 1])
            logger.exception("Notification delivery failed for notification %s", notification.pk)
        notification.save(update_fields=("attempts", "is_sent", "last_error", "next_attempt_at"))

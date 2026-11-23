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
        conversation = send_official_match_message(match, user, their_title)
        notify_user(
            user,
            "Possible lost-and-found match",
            f'A possible match was found for "{their_title}". Open the official Tarmac message to review it.',
            {"kind": "lost_found_match", "match_id": match.pk, "report_title": their_title, "conversation_id": conversation.pk if conversation else None, "link": f"/messages?conversation={conversation.pk}" if conversation else "/lost-found", "notification_category": "lost_found"},
            f"lost-found-match:{match.pk}:{user.pk}",
        )


def send_official_match_message(match, user, report_title, confirmed=False):
    """Create a private, official chat notice for one participant in a match."""
    from apps.accounts.models import User
    from apps.messaging.models import Conversation, Message
    from apps.messaging.serializers import create_participant_snapshot

    administrator = User.objects.filter(is_staff=True, is_active=True, is_suspended=False).order_by("pk").first()
    if not administrator:
        logger.error("Cannot send official lost-and-found message: no active administrator is configured")
        return None
    conversation = Conversation.objects.filter(
        context_type=Conversation.ContextType.OFFICIAL_ALERT,
        context_id=match.pk,
        is_official=True,
        participants=user,
    ).filter(participants=administrator).first()
    if conversation is None:
        conversation = Conversation.objects.create(
            context_type=Conversation.ContextType.OFFICIAL_ALERT,
            context_id=match.pk,
            context_title=f"Lost & Found match: {report_title}"[:180],
            is_official=True,
        )
        conversation.participants.add(user, administrator)
        create_participant_snapshot(conversation, user)
        create_participant_snapshot(conversation, administrator, name="Tarmac Support")
    if confirmed:
        confirmation = f"Tarmac administrators reviewed this candidate and confirmed it as a possible match for ‘{report_title}’. Please compare the identifying details before arranging a return."
        if not conversation.messages.filter(body=confirmation).exists():
            Message.objects.create(conversation=conversation, sender=administrator, body=confirmation)
            conversation.save(update_fields=("updated_at",))
    elif not conversation.messages.exists():
        Message.objects.create(
            conversation=conversation,
            sender=administrator,
            body=(
                f"Tarmac found a possible match for your report, ‘{report_title}’. "
                f"The match score is {match.score}%. Open Lost & Found to review the reports and message the other student if the details look right. "
                "This is an automated suggestion, not confirmation that the item is yours."
            ),
        )
        conversation.save(update_fields=("updated_at",))
    return conversation


def notify_user(user, subject, body, data, event_key, *, dispatch_email=False, force_email=False):
    queue_notification(user, Notification.Channel.IN_APP, subject, body, data, f"{event_key}:in_app")
    from apps.administration.models import PlatformSettings
    platform = PlatformSettings.get_solo()
    kind = (data or {}).get("kind", "")
    category = (data or {}).get("notification_category") or notification_category(kind)
    category_settings = (platform.notification_channels or {}).get(category, {})
    email_notification = None
    if user.email and (force_email or (platform.email_notifications_enabled and category_settings.get("email", True) and user.notification_email_enabled)):
        email_data = {**(data or {}), "_force_email_delivery": True} if force_email else data
        email_notification = queue_notification(user, Notification.Channel.EMAIL, subject, body, email_data, f"{event_key}:email")
    if dispatch_email and email_notification:
        transaction.on_commit(lambda notification_id=email_notification.pk: dispatch_notification(notification_id))
    if category_settings.get("whatsapp", False) and user.notification_whatsapp_enabled and user.whatsapp_consent_at and user.whatsapp_number:
        template_key = whatsapp_template_key((data or {}).get("whatsapp_template_kind") or kind)
        if template_key and os.getenv(template_key):
            queue_notification(user, Notification.Channel.WHATSAPP, subject, body, data, f"{event_key}:whatsapp")


def notification_category(kind):
    if kind.startswith("profile_change"):
        return "account_security"
    if kind.startswith(("lost_found", "item_claim")):
        return "lost_found"
    if kind.startswith("accommodation"):
        return "accommodation"
    if kind.startswith("support"):
        return "support"
    if kind in ("admin_conversation_reply", "official_message"):
        return "official"
    if kind.startswith(("announcement", "promotion", "ad_")):
        return "promotional"
    if kind in ("account_enforcement", "admin_warning", "verification_review", "verification_badge_updated"):
        return "account_security"
    if kind.startswith(("payment", "subscription", "order_")):
        return "payments"
    return "marketplace"


def whatsapp_template_key(kind):
    return {
        "lost_found_match": "WHATSAPP_MATCH_TEMPLATE",
        "lost_found_match_response": "WHATSAPP_MATCH_TEMPLATE",
        "lost_found_returned": "WHATSAPP_MATCH_TEMPLATE",
        "item_claim": "WHATSAPP_MATCH_TEMPLATE",
        "item_claim_review": "WHATSAPP_MATCH_TEMPLATE",
        "support_reply": "WHATSAPP_SUPPORT_TEMPLATE",
        "support_completed": "WHATSAPP_SUPPORT_TEMPLATE",
        "support_ticket_reopened": "WHATSAPP_SUPPORT_TEMPLATE",
        "admin_conversation_reply": "WHATSAPP_OFFICIAL_TEMPLATE",
        "official_message": "WHATSAPP_OFFICIAL_TEMPLATE",
        "promotion": "WHATSAPP_PROMOTION_TEMPLATE",
        "account_enforcement": "WHATSAPP_SECURITY_TEMPLATE",
        "admin_warning": "WHATSAPP_SECURITY_TEMPLATE",
        "order_update": "WHATSAPP_ORDER_TEMPLATE",
        "order_created": "WHATSAPP_ORDER_TEMPLATE",
        "listing_active": "WHATSAPP_LISTING_TEMPLATE",
        "listing_review": "WHATSAPP_LISTING_TEMPLATE",
        "ad_review": "WHATSAPP_AD_TEMPLATE",
        "ad_approved": "WHATSAPP_AD_TEMPLATE",
        "payment_received": "WHATSAPP_ORDER_TEMPLATE",
        "subscription_activated": "WHATSAPP_ORDER_TEMPLATE",
    }.get(kind)


def _send_email(notification):
    user = notification.user
    data = notification.data or {}
    kind = data.get("kind", "")
    origin = os.getenv("FRONTEND_ORIGIN", "http://localhost:3000").rstrip("/")
    action_url = f"{origin}/dashboard"
    action_label = "Open Tarmac"
    if kind.startswith("lost_found"):
        conversation_id = data.get("conversation_id")
        action_url, action_label = (f"{origin}/messages?conversation={conversation_id}", "Open official match message") if conversation_id else (f"{origin}/lost-found", "Review possible match")
    elif kind in ("order_update", "order_created"):
        product_id = data.get("product_id")
        action_url = f"{origin}/marketplace/{product_id}" if product_id else f"{origin}/dashboard"
        action_label = "View order update"
    elif kind.startswith("profile_change"):
        if user.is_staff:
            action_url, action_label = f"{origin}/admin/profile-changes", "Review profile request"
        else:
            action_url = f"{origin}/profile#profile-change-requests"
            action_label = "Reply in Tarmac" if kind == "profile_change_question" else "View profile request"
    elif kind == "admin_conversation_reply":
        conversation_id = data.get("conversation_id")
        action_url = f"{origin}/messages?conversation={conversation_id}" if conversation_id else f"{origin}/messages"
        action_label = "Open official message"
    elif kind == "account_enforcement":
        action_url = data.get("appeal_url") or f"{origin}/login"
        action_label = "Submit an appeal" if data.get("appeal_url") else "Sign in to Tarmac"
    elif kind in ("conversation_completed", "conversation_completion_requested"):
        conversation_id = data.get("conversation_id")
        action_url = f"{origin}/messages?conversation={conversation_id}" if conversation_id else f"{origin}/messages"
        action_label = "Open Tarmac conversation"
    elif kind == "verification_review":
        if data.get("status") == "rejected":
            action_url, action_label = f"{origin}/verify-student?resubmit=1", "Review and resubmit verification"
        else:
            action_url, action_label = f"{origin}/dashboard", "Continue to Tarmac"
    elif kind in ("item_claim", "item_claim_review"):
        action_url, action_label = f"{origin}/lost-found", "Review item claim"
    elif kind.startswith("support"):
        if not user.is_active or user.is_suspended:
            support_email = os.getenv("SUPPORT_EMAIL", "")
            if not support_email:
                from apps.administration.models import PlatformSettings
                support_email = PlatformSettings.get_solo().support_email
            action_url = f"mailto:{support_email}" if support_email else ""
            action_label = "Reply to Tarmac Support" if support_email else ""
        else:
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
    from apps.administration.models import PlatformSettings
    platform = PlatformSettings.get_solo()
    phone_number_id = platform.whatsapp_phone_number_id or os.getenv("WHATSAPP_PHONE_NUMBER_ID")
    template_key = whatsapp_template_key(notification.data.get("whatsapp_template_kind") or notification.data.get("kind", ""))
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
        if notification.channel != Notification.Channel.IN_APP:
            from apps.administration.models import PlatformSettings
            platform = PlatformSettings.get_solo()
            data = notification.data or {}
            category = data.get("notification_category") or notification_category(data.get("kind", ""))
            category_channels = (platform.notification_channels or {}).get(category, {})
            forced_email = data.get("_force_email_delivery") is True
            if notification.channel == Notification.Channel.EMAIL and not forced_email and not (
                platform.email_notifications_enabled and category_channels.get("email", True) and notification.user.notification_email_enabled
            ):
                notification.is_sent = True
                notification.last_error = "Skipped because email delivery is disabled by the user or administrator."
                notification.save(update_fields=("is_sent", "last_error"))
                return
            if notification.channel == Notification.Channel.WHATSAPP and not (
                category_channels.get("whatsapp", False)
                and notification.user.notification_whatsapp_enabled
                and notification.user.whatsapp_consent_at
            ):
                notification.is_sent = True
                notification.last_error = "Skipped because WhatsApp delivery is disabled by the user or administrator."
                notification.save(update_fields=("is_sent", "last_error"))
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

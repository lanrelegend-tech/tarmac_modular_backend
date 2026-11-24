from apps.accounts.models import User
from .models import Notification
from .services import queue_notification, queue_push_notification


STAFF_NOTIFICATION_SECTIONS = {
    "verification": {"label": "Student verification", "link": "/admin/verification"},
    "profile_changes": {"label": "Profile change requests", "link": "/admin/profile-changes"},
    "marketplace": {"label": "Marketplace", "link": "/admin/marketplace"},
    "jobs": {"label": "Jobs & Gigs", "link": "/admin/jobs"},
    "lost_found": {"label": "Lost & Found", "link": "/admin/lost-found"},
    "accommodation": {"label": "Accommodation", "link": "/admin/accommodation"},
    "reports": {"label": "Reports", "link": "/admin/reports"},
    "reviews": {"label": "Reviews", "link": "/admin/reviews"},
    "ads": {"label": "Advertisements", "link": "/admin/advertisements"},
    "support": {"label": "Support", "link": "/admin/support"},
}


def valid_staff_sections(value):
    return isinstance(value, list) and all(isinstance(item, str) and item in STAFF_NOTIFICATION_SECTIONS for item in value) and len(value) == len(set(value))


def staff_has_section(user, section):
    return bool(user.is_superuser or section in (user.staff_notification_sections or []))


def notify_staff_section(section, subject, body, data, event_key):
    """Send a deduplicated in-app/web-push alert only to staff assigned to a queue."""
    config = STAFF_NOTIFICATION_SECTIONS.get(section)
    if not config:
        return
    payload = {**(data or {}), "kind": "admin_queue", "staff_section": section, "link": config["link"]}
    recipients = User.objects.filter(is_staff=True, is_active=True, is_suspended=False).only(
        "id", "is_superuser", "staff_notification_sections"
    )
    for staff_user in recipients.iterator(chunk_size=200):
        if not staff_has_section(staff_user, section):
            continue
        key = f"staff-queue:{event_key}:{staff_user.pk}"[:180]
        _, created = queue_notification(staff_user, Notification.Channel.IN_APP, subject, body, payload, key)
        if created:
            queue_push_notification(staff_user, subject, body, payload, key)

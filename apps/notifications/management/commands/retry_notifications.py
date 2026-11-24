from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from apps.notifications.models import Notification
from apps.notifications.services import MAX_DELIVERY_ATTEMPTS, dispatch_notification


def process_pending_notifications(limit=500):
    due = Notification.objects.filter(
        is_sent=False,
        attempts__lt=MAX_DELIVERY_ATTEMPTS,
    ).filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=timezone.now()))
    ids = list(due.order_by("pk").values_list("pk", flat=True)[:limit])
    for notification_id in ids:
        dispatch_notification(notification_id)
    return len(ids)


class Command(BaseCommand):
    help = "Deliver queued email, WhatsApp, and web-push notifications."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=500)

    def handle(self, *args, **options):
        limit = max(1, min(int(options["limit"]), 5000))
        processed = process_pending_notifications(limit)
        self.stdout.write(self.style.SUCCESS(f"Processed {processed} pending notification(s)."))

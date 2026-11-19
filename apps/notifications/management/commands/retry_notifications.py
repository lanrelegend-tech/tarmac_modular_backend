from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from apps.notifications.models import Notification
from apps.notifications.services import RETRY_MINUTES, dispatch_notification


class Command(BaseCommand):
    help = "Retry failed email and WhatsApp notification deliveries."

    def handle(self, *args, **options):
        due = Notification.objects.filter(
            is_sent=False,
            attempts__lt=len(RETRY_MINUTES),
        ).filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=timezone.now()))
        processed = 0
        last_id = 0
        while True:
            ids = list(due.filter(pk__gt=last_id).order_by("pk").values_list("pk", flat=True)[:500])
            if not ids:
                break
            for notification_id in ids:
                dispatch_notification(notification_id)
                processed += 1
            last_id = ids[-1]
        self.stdout.write(self.style.SUCCESS(f"Processed {processed} pending notification(s)."))

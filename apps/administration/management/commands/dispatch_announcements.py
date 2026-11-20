from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.administration.models import Announcement, PlatformSettings
from apps.notifications.models import Notification


class Command(BaseCommand):
    help = "Deliver published school announcements to verified students in bounded batches."

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=1000)

    def handle(self, *args, **options):
        batch_size = max(1, min(options["batch_size"], 5000))
        platform = PlatformSettings.get_solo()
        if not platform.announcements_enabled:
            self.stdout.write("Announcements are disabled; no notifications were sent.")
            return
        delivered = 0
        ids = Announcement.objects.filter(is_published=True, delivery_completed=False).order_by("pk").values_list("pk", flat=True)
        for announcement_id in ids.iterator(chunk_size=100):
            with transaction.atomic():
                announcement = Announcement.objects.select_for_update().filter(pk=announcement_id, is_published=True, delivery_completed=False).first()
                if announcement is None:
                    continue
                recipients = User.objects.filter(pk__gt=announcement.delivery_cursor, is_active=True, is_suspended=False)
                if platform.student_verification_enabled:
                    recipients = recipients.filter(is_student_verified=True)
                if platform.email_verification_required:
                    recipients = recipients.filter(email_verified=True)
                if announcement.audience == Announcement.Audience.DEPARTMENT:
                    recipients = recipients.filter(department__iexact=announcement.department)
                recipient_ids = list(recipients.order_by("pk").values_list("pk", flat=True)[:batch_size])
                if recipient_ids:
                    now = timezone.now()
                    Notification.objects.bulk_create([
                        Notification(user_id=user_id, channel=Notification.Channel.IN_APP, subject=announcement.title,
                            body=announcement.body, data={"kind":"announcement", "display":"popup", "announcement_id":announcement.pk},
                            is_sent=True, dedupe_key=f"announcement:{announcement.pk}:{user_id}:in_app", created_at=now)
                        for user_id in recipient_ids
                    ], ignore_conflicts=True)
                    announcement.delivery_cursor = recipient_ids[-1]
                    announcement.recipient_count += len(recipient_ids)
                    announcement.delivery_completed = len(recipient_ids) < batch_size
                    delivered += len(recipient_ids)
                else:
                    announcement.delivery_completed = True
                announcement.save(update_fields=("delivery_cursor", "recipient_count", "delivery_completed"))
        self.stdout.write(self.style.SUCCESS(f"Queued {delivered} announcement notification(s)."))

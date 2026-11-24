import logging
import time

from django.core.management.base import BaseCommand

from .retry_notifications import process_pending_notifications


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Continuously deliver queued email, WhatsApp, and web-push notifications."

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=50)
        parser.add_argument("--poll-seconds", type=float, default=2.0)

    def handle(self, *args, **options):
        batch_size = max(1, min(int(options["batch_size"]), 500))
        poll_seconds = max(0.25, float(options["poll_seconds"]))
        self.stdout.write("Notification worker started.")
        while True:
            try:
                processed = process_pending_notifications(batch_size)
                if processed == 0:
                    time.sleep(poll_seconds)
            except KeyboardInterrupt:
                self.stdout.write("Notification worker stopped.")
                return
            except Exception:
                logger.exception("Notification worker batch failed")
                time.sleep(poll_seconds)

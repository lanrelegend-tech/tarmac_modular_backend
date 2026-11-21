from django.core.management.base import BaseCommand
from django.utils import timezone
from apps.jobs.models import JobListing


class Command(BaseCommand):
    help = "Mark active Jobs & Gigs listings as expired after their application deadline."

    def handle(self, *args, **options):
        count = JobListing.objects.filter(
            status=JobListing.Status.ACTIVE,
            deadline__lt=timezone.localdate(),
        ).update(status=JobListing.Status.EXPIRED, closed_at=timezone.now())
        self.stdout.write(self.style.SUCCESS(f"Expired {count} job listing(s)."))

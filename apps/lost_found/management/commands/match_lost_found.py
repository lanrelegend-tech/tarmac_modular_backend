from django.core.management.base import BaseCommand
from django.db import transaction

from apps.lost_found.models import LostFoundReport
from apps.lost_found.services import process_possible_matches


class Command(BaseCommand):
    help = "Process queued lost-and-found reports and create likely match candidates."

    def handle(self, *args, **options):
        processed = 0
        last_id = 0
        while True:
            report_ids = list(LostFoundReport.objects.filter(matching_processed=False, is_active=True, pk__gt=last_id).order_by("pk").values_list("pk", flat=True)[:100])
            if not report_ids:
                break
            for report_id in report_ids:
                with transaction.atomic():
                    report = LostFoundReport.objects.select_for_update().filter(pk=report_id, matching_processed=False, is_active=True).first()
                    if report is None:
                        continue
                    process_possible_matches(report)
                    report.matching_processed = True
                    report.save(update_fields=("matching_processed",))
                    processed += 1
            last_id = report_ids[-1]
        self.stdout.write(self.style.SUCCESS(f"Processed {processed} lost-and-found report(s)."))

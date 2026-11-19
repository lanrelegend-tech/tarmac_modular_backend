from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.products.models import Product


class Command(BaseCommand):
    help = "Mark active listings past their expiry date as expired."

    def handle(self, *args, **options):
        count = Product.objects.filter(status=Product.Status.ACTIVE, expires_at__lte=timezone.now()).update(status=Product.Status.EXPIRED)
        self.stdout.write(self.style.SUCCESS(f"Expired {count} listing(s)."))

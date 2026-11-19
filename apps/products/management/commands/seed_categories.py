from django.core.management.base import BaseCommand
from django.utils.text import slugify

from apps.products.models import Category


class Command(BaseCommand):
    help = "Create Tarmac's initial marketplace categories without duplicating existing categories."

    def handle(self, *args, **options):
        names = ("Electronics", "Generators", "Furniture", "Phones", "Clothing", "Shoes", "Books", "Kitchen equipment", "Appliances", "Others")
        created = 0
        for name in names:
            _category, was_created = Category.objects.get_or_create(name=name, defaults={"slug": slugify(name)})
            created += was_created
        self.stdout.write(self.style.SUCCESS(f"Created {created} category(ies)."))

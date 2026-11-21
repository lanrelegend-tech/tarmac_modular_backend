from django.db import migrations
from django.utils.text import slugify


CATEGORIES = {
    "job": ("Part-time jobs", "Full-time jobs", "Internships", "Temporary jobs", "Other employment"),
    "service": ("Graphic design", "Photography", "Video editing", "Programming", "Hairdressing", "Makeup", "Laundry", "Private tutorials", "Catering", "Delivery", "Other student services"),
}


def seed_categories(apps, schema_editor):
    Category = apps.get_model("jobs", "JobCategory")
    database = schema_editor.connection.alias
    for kind, names in CATEGORIES.items():
        for name in names:
            Category.objects.using(database).get_or_create(
                slug=slugify(name), defaults={"name": name, "category_type": kind, "is_active": True}
            )


class Migration(migrations.Migration):
    dependencies = [("jobs", "0001_initial")]
    operations = [migrations.RunPython(seed_categories, migrations.RunPython.noop)]

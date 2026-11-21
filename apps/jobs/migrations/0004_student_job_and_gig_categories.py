from django.db import migrations
from django.utils.text import slugify


JOB_CATEGORIES = (
    "Campus Assistant Roles",
    "Event Crew & Ushers",
    "Other Student Jobs",
    "Tutoring & Teaching",
    "Retail & Sales",
    "Delivery & Errands",
    "Research & Data Collection",
    "Office & Admin Support",
    "Customer Service",
    "Social Media & Promotion",
    "Student Projects & Assistance",
)

SERVICE_CATEGORIES = (
    "Tailoring & Alterations",
    "Painting & Decoration",
    "Plumbing",
    "Electrical Repairs",
    "Carpentry & Furniture",
    "Phone & Computer Repairs",
    "Cleaning",
    "Laundry",
    "Hairdressing & Barbering",
    "Makeup & Beauty",
    "Catering & Baking",
    "Photography",
    "Graphic Design",
    "Video Editing",
    "Web & App Development",
    "Tutoring & Lessons",
    "Delivery & Errands",
    "Event Services",
    "Printing & Typing",
    "Mechanic & Auto Services",
    "Gardening & Landscaping",
    "Other Student Services",
)


LEGACY_CATEGORY_NAMES = {
    "part-time-jobs": "Campus Assistant Roles",
    "full-time-jobs": "Retail & Sales",
    "internships": "Research & Data Collection",
    "temporary-jobs": "Event Crew & Ushers",
    "other-employment": "Other Student Jobs",
}


def update_categories(apps, schema_editor):
    Category = apps.get_model("jobs", "JobCategory")
    database = schema_editor.connection.alias

    for old_slug, new_name in LEGACY_CATEGORY_NAMES.items():
        old = Category.objects.using(database).filter(slug=old_slug).first()
        if old is None:
            continue
        new_slug = slugify(new_name)
        duplicate = Category.objects.using(database).filter(slug=new_slug).exclude(pk=old.pk).first()
        if duplicate:
            # Preserve listings attached to either category and remove the old row.
            JobListing = apps.get_model("jobs", "JobListing")
            JobListing.objects.using(database).filter(category_id=old.pk).update(category_id=duplicate.pk)
            old.delete(using=database)
        else:
            old.name = new_name
            old.slug = new_slug
            old.save(using=database, update_fields=("name", "slug"))

    for kind, names in (("job", JOB_CATEGORIES), ("service", SERVICE_CATEGORIES)):
        for name in names:
            Category.objects.using(database).get_or_create(
                slug=slugify(name),
                defaults={"name": name, "category_type": kind, "is_active": True},
            )


class Migration(migrations.Migration):
    dependencies = [("jobs", "0003_joblisting_anonymous_identity_and_more")]

    operations = [migrations.RunPython(update_categories, migrations.RunPython.noop)]

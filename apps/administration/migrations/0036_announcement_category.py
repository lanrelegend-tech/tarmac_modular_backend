from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("administration", "0035_announcementpublisher"),
    ]

    operations = [
        migrations.AddField(
            model_name="announcement",
            name="category",
            field=models.CharField(
                choices=[
                    ("education", "Education"),
                    ("emergency", "Emergency"),
                    ("academic", "Academic"),
                    ("examinations", "Examinations"),
                    ("events", "Events"),
                    ("security", "Safety & security"),
                    ("marketplace", "Marketplace"),
                    ("lost_found", "Lost & found"),
                    ("jobs", "Jobs & gigs"),
                    ("accommodation", "Accommodation"),
                    ("general", "General"),
                ],
                db_index=True,
                default="general",
                max_length=24,
            ),
        ),
    ]

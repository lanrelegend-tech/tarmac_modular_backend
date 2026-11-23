from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("administration", "0033_announcement_archived_at_announcement_expires_at_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="announcement",
            name="image",
            field=models.ImageField(blank=True, null=True, upload_to="announcements/"),
        ),
    ]

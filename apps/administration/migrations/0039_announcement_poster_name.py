from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("administration", "0038_announcementimage"),
    ]

    operations = [
        migrations.AddField(
            model_name="announcement",
            name="poster_name",
            field=models.CharField(default="KWASU", max_length=120),
        ),
    ]

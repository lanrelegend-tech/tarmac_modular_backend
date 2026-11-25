from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("jobs", "0007_joblisting_public_id")]

    operations = [
        migrations.AddField(
            model_name="joblisting",
            name="submission_key",
            field=models.UUIDField(blank=True, editable=False, null=True, unique=True),
        ),
    ]

from django.db import migrations, models
import apps.administration.models


class Migration(migrations.Migration):

    dependencies = [
        ("administration", "0031_emergencycontact_campus"),
    ]

    operations = [
        migrations.AddField(
            model_name="platformsettings",
            name="campus_areas",
            field=models.JSONField(blank=True, default=apps.administration.models.default_campus_areas),
        ),
    ]

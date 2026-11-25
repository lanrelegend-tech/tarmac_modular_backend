from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0025_user_staff_notification_sections")]

    operations = [
        migrations.AddField(
            model_name="courseprogram",
            name="faculty",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
    ]

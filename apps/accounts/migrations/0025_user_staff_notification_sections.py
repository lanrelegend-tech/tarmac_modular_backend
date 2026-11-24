from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0024_create_missing_signup_verifications")]

    operations = [
        migrations.AddField(
            model_name="user",
            name="staff_notification_sections",
            field=models.JSONField(blank=True, default=list),
        ),
    ]

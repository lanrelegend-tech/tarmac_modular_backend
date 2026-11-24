from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("notifications", "0004_notification_notification_unread_idx"),
    ]

    operations = [
        migrations.AlterField(
            model_name="notification",
            name="channel",
            field=models.CharField(
                choices=[
                    ("email", "Email"),
                    ("whatsapp", "WhatsApp"),
                    ("in_app", "In-app"),
                    ("push", "Phone push"),
                ],
                max_length=12,
            ),
        ),
    ]

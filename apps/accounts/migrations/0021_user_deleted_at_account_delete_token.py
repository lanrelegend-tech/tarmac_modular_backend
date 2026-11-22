from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0020_user_username_changed_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="deleted_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AlterField(
            model_name="accountactiontoken",
            name="purpose",
            field=models.CharField(
                choices=[
                    ("email_verify", "Email verification"),
                    ("password_reset", "Password reset"),
                    ("suspension_appeal", "Suspension appeal"),
                    ("account_delete", "Account deletion confirmation"),
                ],
                max_length=20,
            ),
        ),
    ]

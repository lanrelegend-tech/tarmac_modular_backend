from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0019_profilechangerequest_original_values"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="username_changed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]

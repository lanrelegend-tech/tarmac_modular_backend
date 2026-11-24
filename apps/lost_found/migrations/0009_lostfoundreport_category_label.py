from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("lost_found", "0008_lostfoundreport_deleted_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="lostfoundreport",
            name="category_label",
            field=models.CharField(blank=True, max_length=80),
        ),
    ]

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("lost_found", "0009_lostfoundreport_category_label"),
    ]

    operations = [
        migrations.AddField(
            model_name="lostfoundreport",
            name="reward",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="lostfoundreport",
            name="reviewer_note",
            field=models.TextField(blank=True),
        ),
        migrations.AlterField(
            model_name="lostfoundreport",
            name="status",
            field=models.CharField(choices=[("pending_review", "Pending review"), ("open", "Open"), ("matched", "Potential match"), ("returned", "Returned"), ("rejected", "Rejected"), ("closed", "Closed")], default="pending_review", max_length=20),
        ),
        migrations.AlterField(
            model_name="lostfoundreport",
            name="is_active",
            field=models.BooleanField(default=False),
        ),
    ]

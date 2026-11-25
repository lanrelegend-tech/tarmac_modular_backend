from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("lost_found", "0010_lostfoundreport_review_reward"),
    ]

    operations = [
        migrations.AddField(
            model_name="lostfoundreport",
            name="submission_key",
            field=models.UUIDField(blank=True, editable=False, null=True),
        ),
        migrations.AddConstraint(
            model_name="lostfoundreport",
            constraint=models.UniqueConstraint(
                condition=models.Q(submission_key__isnull=False),
                fields=("reporter", "submission_key"),
                name="unique_lost_found_submission_key",
            ),
        ),
    ]

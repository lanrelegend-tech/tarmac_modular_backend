from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accommodation", "0006_alter_accommodationlisting_status")]

    operations = [
        migrations.AddField(
            model_name="accommodationlisting",
            name="submission_key",
            field=models.UUIDField(blank=True, editable=False, null=True),
        ),
        migrations.AddConstraint(
            model_name="accommodationlisting",
            constraint=models.UniqueConstraint(
                condition=models.Q(submission_key__isnull=False),
                fields=("owner", "submission_key"),
                name="accom_owner_submission_key_uniq",
            ),
        ),
    ]

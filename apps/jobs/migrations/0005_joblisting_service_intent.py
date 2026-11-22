from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("jobs", "0004_student_job_and_gig_categories")]

    operations = [
        migrations.AddField(
            model_name="joblisting",
            name="service_intent",
            field=models.CharField(
                choices=[("offer", "Offering a service"), ("request", "Looking for a service")],
                db_index=True,
                default="offer",
                max_length=8,
            ),
        ),
    ]

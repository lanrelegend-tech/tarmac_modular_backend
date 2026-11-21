from django.db import migrations, models


def normalize_staff_roles(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    User.objects.filter(is_superuser=True).update(role="super_admin")
    User.objects.filter(is_staff=True, is_superuser=False).exclude(role="moderator").update(role="admin")


class Migration(migrations.Migration):
    dependencies = [("accounts", "0017_purge_decided_student_verification_images")]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="role",
            field=models.CharField(
                choices=[
                    ("student", "Student"),
                    ("moderator", "Moderator"),
                    ("admin", "Admin"),
                    ("super_admin", "Super admin"),
                ],
                default="student",
                max_length=20,
            ),
        ),
        migrations.RunPython(normalize_staff_roles, migrations.RunPython.noop),
    ]

from django.db import migrations


def create_missing_signup_verifications(apps, schema_editor):
    database = schema_editor.connection.alias
    User = apps.get_model("accounts", "User")
    Verification = apps.get_model("accounts", "StudentVerification")
    PlatformSettings = apps.get_model("administration", "PlatformSettings")

    platform = PlatformSettings.objects.using(database).first()
    if not platform or not platform.student_verification_enabled or platform.auto_approve_students:
        return

    existing_user_ids = Verification.objects.using(database).values("user_id")
    users = User.objects.using(database).filter(
        role="student",
        is_active=True,
        is_student_verified=False,
    ).exclude(matric_number__isnull=True).exclude(matric_number="").exclude(pk__in=existing_user_ids)

    batch = []
    for user_id, matric_number in users.values_list("pk", "matric_number").iterator(chunk_size=1000):
        batch.append(
            Verification(
                user_id=user_id,
                submitted_matric_number=matric_number,
                requested_affiliation="student",
                status="pending",
            )
        )
        if len(batch) == 500:
            Verification.objects.using(database).bulk_create(batch, batch_size=500)
            batch = []
    if batch:
        Verification.objects.using(database).bulk_create(batch, batch_size=500)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0023_user_area"),
        ("administration", "0032_platformsettings_campus_areas"),
    ]

    operations = [
        migrations.RunPython(create_missing_signup_verifications, migrations.RunPython.noop),
    ]

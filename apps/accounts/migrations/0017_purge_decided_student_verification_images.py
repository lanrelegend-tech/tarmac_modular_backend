from django.db import migrations


def purge_decided_student_verification_images(apps, schema_editor):
    Verification = apps.get_model("accounts", "StudentVerification")
    database = schema_editor.connection.alias
    decided = Verification.objects.using(database).filter(
        status__in=("approved", "rejected")
    ).exclude(student_id_image="")

    for verification in decided.iterator():
        image = verification.student_id_image
        if image and image.name:
            # Remove the backing Cloudinary or local media object, then erase
            # the stored path so it cannot be fetched through the admin API.
            image.delete(save=False)
            Verification.objects.using(database).filter(pk=verification.pk).update(
                student_id_image=None
            )


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0016_user_public_profile_picture_enabled"),
    ]

    operations = [
        migrations.RunPython(
            purge_decided_student_verification_images,
            migrations.RunPython.noop,
        ),
    ]

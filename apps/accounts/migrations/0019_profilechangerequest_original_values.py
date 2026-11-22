from django.db import migrations, models


def backfill_original_values(apps, schema_editor):
    ProfileChangeRequest = apps.get_model("accounts", "ProfileChangeRequest")
    User = apps.get_model("accounts", "User")
    database = schema_editor.connection.alias
    requests = ProfileChangeRequest.objects.using(database).filter(
        status__in=("pending", "needs_info"),
        original_values={},
    ).iterator()
    for change_request in requests:
        user = User.objects.using(database).filter(pk=change_request.user_id).first()
        if not user:
            continue
        original_values = {
            field: str(getattr(user, field, "") or "")
            for field in (change_request.requested_changes or {})
        }
        if change_request.requested_profile_picture:
            original_values["profile_picture"] = "Profile photo on account" if user.profile_picture else "No profile photo"
        change_request.original_values = original_values
        change_request.save(using=database, update_fields=("original_values",))


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0018_user_moderator_role"),
    ]

    operations = [
        migrations.AddField(
            model_name="profilechangerequest",
            name="original_values",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.RunPython(backfill_original_values, migrations.RunPython.noop),
    ]

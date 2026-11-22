from django.db import migrations


def add_nigeria_emergency_line(apps, schema_editor):
    EmergencyContact = apps.get_model("administration", "EmergencyContact")
    EmergencyContact.objects.using(schema_editor.connection.alias).get_or_create(
        phone_number="112",
        defaults={
            "name": "Nigeria emergency response (Police, Ambulance & Fire)",
            "description": "Dial 112 for national emergency assistance. The emergency centre can route police, ambulance, fire and other response services.",
            "location": "Nigeria",
            "sort_order": 0,
            "is_active": True,
        },
    )


def remove_nigeria_emergency_line(apps, schema_editor):
    EmergencyContact = apps.get_model("administration", "EmergencyContact")
    EmergencyContact.objects.using(schema_editor.connection.alias).filter(
        phone_number="112", name="Nigeria emergency response (Police, Ambulance & Fire)"
    ).delete()


class Migration(migrations.Migration):
    dependencies = [("administration", "0028_emergencycontact")]
    operations = [migrations.RunPython(add_nigeria_emergency_line, remove_nigeria_emergency_line)]

from rest_framework import serializers

from .models import PlatformSettings


def validate_campus_choice(value):
    selected = str(value or "").strip()
    if not selected:
        raise serializers.ValidationError("Choose a campus.")
    campuses = PlatformSettings.get_solo().campuses or []
    for campus in campuses:
        if selected.casefold() == str(campus).casefold():
            return campus
    raise serializers.ValidationError("Choose a campus from the list set by the Tarmac administrators.")


def clean_campus_list(values):
    if not isinstance(values, list) or len(values) > 30:
        raise serializers.ValidationError("Add up to 30 campus names.")
    cleaned = []
    seen = set()
    for item in values:
        if not isinstance(item, str) or len(item.strip()) > 120:
            raise serializers.ValidationError("Each campus name must be text up to 120 characters.")
        name = item.strip()
        if not name:
            continue
        key = name.casefold()
        if key not in seen:
            cleaned.append(name)
            seen.add(key)
    return cleaned

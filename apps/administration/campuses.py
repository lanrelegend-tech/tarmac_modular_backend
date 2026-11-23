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


def clean_campus_areas(value, campuses):
    if not isinstance(value, dict):
        raise serializers.ValidationError("Set areas as a list under each campus.")
    allowed = {str(campus).casefold(): campus for campus in campuses}
    cleaned = {}
    total = 0
    for campus_name, areas in value.items():
        if not isinstance(campus_name, str) or campus_name.strip().casefold() not in allowed:
            raise serializers.ValidationError("Areas must belong to a campus in the campus list.")
        if not isinstance(areas, list) or len(areas) > 100:
            raise serializers.ValidationError("Add up to 100 areas for each campus.")
        campus = allowed[campus_name.strip().casefold()]
        names = []
        seen = set()
        for area in areas:
            if not isinstance(area, str) or len(area.strip()) > 120:
                raise serializers.ValidationError("Each area must be text up to 120 characters.")
            name = area.strip()
            if not name:
                continue
            key = name.casefold()
            if key not in seen:
                names.append(name)
                seen.add(key)
        cleaned[campus] = names
        total += len(names)
    if total > 500:
        raise serializers.ValidationError("Add up to 500 areas across all campuses.")
    return cleaned


def validate_area_choice(campus, value, *, required_when_configured=True):
    selected = str(value or "").strip()
    settings = PlatformSettings.get_solo()
    campus_name = validate_campus_choice(campus)
    areas = (settings.campus_areas or {}).get(campus_name, [])
    if not selected:
        if required_when_configured and areas:
            raise serializers.ValidationError("Choose an area for the selected campus.")
        return ""
    for area in areas:
        if selected.casefold() == str(area).casefold():
            return area
    if areas:
        raise serializers.ValidationError("Choose an area from the list set by the Tarmac administrators.")
    raise serializers.ValidationError("This campus has no areas configured yet.")

import calendar


def username_change_available_at(changed_at):
    """Return the timestamp three calendar months after a username change."""
    month_index = changed_at.month - 1 + 3
    year = changed_at.year + month_index // 12
    month = month_index % 12 + 1
    day = min(changed_at.day, calendar.monthrange(year, month)[1])
    return changed_at.replace(year=year, month=month, day=day)

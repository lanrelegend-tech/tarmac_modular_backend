from datetime import timedelta
from difflib import SequenceMatcher

from django.db.models import Q

from apps.notifications.services import notify_possible_match
from .models import ItemMatch, LostFoundReport


def create_possible_matches(report):
    """Queue matching for the scheduled worker instead of the request path."""
    LostFoundReport.objects.filter(pk=report.pk).update(matching_processed=False)


def process_possible_matches(report):
    """Calculate candidates in memory and use bounded bulk database queries."""
    opposite = LostFoundReport.Type.FOUND if report.report_type == LostFoundReport.Type.LOST else LostFoundReport.Type.LOST
    date_window = Q(incident_date__range=(report.incident_date - timedelta(days=30), report.incident_date + timedelta(days=30)))
    candidate_scope = date_window | Q(category_id=report.category_id) if report.category_id else date_window
    candidates = list(
        LostFoundReport.objects.filter(
            report_type=opposite,
            is_active=True,
            status__in=(LostFoundReport.Status.OPEN, LostFoundReport.Status.MATCHED),
        )
        .filter(candidate_scope)
        .exclude(pk=report.pk)
        .only("id", "title", "description", "colour", "category_id", "location", "incident_date")
        .order_by("-created_at")[:500]
    )
    if not candidates:
        return

    candidate_ids = [candidate.pk for candidate in candidates]
    existing_query = (
        Q(lost_report_id=report.pk, found_report_id__in=candidate_ids)
        | Q(found_report_id=report.pk, lost_report_id__in=candidate_ids)
    )
    existing = list(ItemMatch.objects.filter(existing_query))
    existing_by_candidate = {}
    for match in existing:
        other_id = match.found_report_id if match.lost_report_id == report.pk else match.lost_report_id
        existing_by_candidate[other_id] = match

    updates = []
    to_create = []
    new_pairs = set()
    report_ids_to_mark = {report.pk}

    for other in candidates:
        lost, found = (report, other) if report.report_type == LostFoundReport.Type.LOST else (other, report)
        score = 0
        reasons = []
        title_similarity = SequenceMatcher(None, lost.title.lower(), found.title.lower()).ratio()
        description_similarity = SequenceMatcher(None, lost.description.lower(), found.description.lower()).ratio()
        if title_similarity > 0.45:
            score += 40
            reasons.append("Similar item title")
        if description_similarity > 0.35:
            score += 20
            reasons.append("Similar description")
        if lost.colour and found.colour and lost.colour.lower() == found.colour.lower():
            score += 20
            reasons.append("Same colour")
        if lost.category_id and lost.category_id == found.category_id:
            score += 10
            reasons.append("Same category")
        if lost.location.lower() == found.location.lower():
            score += 10
            reasons.append("Same location")
        if abs((lost.incident_date - found.incident_date).days) <= 30:
            score += 10
            reasons.append("Reported within 30 days")

        match = existing_by_candidate.get(other.pk)
        if score < 40:
            if match and not match.is_reviewed and not match.is_dismissed:
                match.is_dismissed = True
                updates.append(match)
            continue
        if match:
            if not match.is_reviewed and not match.is_dismissed:
                match.score = min(score, 100)
                match.reasons = reasons
                updates.append(match)
            continue

        to_create.append(ItemMatch(
            lost_report_id=lost.pk,
            found_report_id=found.pk,
            score=min(score, 100),
            reasons=reasons,
        ))
        new_pairs.add((lost.pk, found.pk))
        report_ids_to_mark.add(other.pk)

    if updates:
        ItemMatch.objects.bulk_update(updates, ("score", "reasons", "is_dismissed"), batch_size=500)
    if not to_create:
        return

    # The unique pair constraint handles overlapping worker batches. Notifications
    # use a per-match dedupe key, so a retry cannot send a duplicate alert.
    ItemMatch.objects.bulk_create(to_create, ignore_conflicts=True, batch_size=500)
    LostFoundReport.objects.filter(pk__in=report_ids_to_mark).update(status=LostFoundReport.Status.MATCHED)
    new_matches = ItemMatch.objects.filter(
        (Q(lost_report_id=report.pk, found_report_id__in=candidate_ids)
         | Q(found_report_id=report.pk, lost_report_id__in=candidate_ids))
    ).select_related("lost_report__reporter", "found_report__reporter")
    for match in new_matches:
        if (match.lost_report_id, match.found_report_id) in new_pairs:
            notify_possible_match(match)
            from apps.notifications.staff import notify_staff_section
            notify_staff_section("lost_found", "Potential Lost & Found match needs review", f'A possible match was detected for "{match.lost_report.title}".', {"kind": "item_match_review", "match_id": match.pk}, f"item-match-review:{match.pk}")

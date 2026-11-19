from datetime import timedelta
from difflib import SequenceMatcher
from django.db.models import Q
from .models import LostFoundReport,ItemMatch
from apps.notifications.services import notify_possible_match

def create_possible_matches(report):
    """Compatibility hook: matching runs in the scheduled worker, outside API requests."""
    LostFoundReport.objects.filter(pk=report.pk).update(matching_processed=False)


def process_possible_matches(report):
    """Create reviewable candidate matches; this is a heuristic, not proof of ownership."""
    opposite=LostFoundReport.Type.FOUND if report.report_type==LostFoundReport.Type.LOST else LostFoundReport.Type.LOST
    date_window = Q(incident_date__range=(report.incident_date-timedelta(days=30), report.incident_date+timedelta(days=30)))
    scope = Q(category_id=report.category_id) if report.category_id else Q()
    candidates=LostFoundReport.objects.filter(report_type=opposite,is_active=True,status__in=(LostFoundReport.Status.OPEN,LostFoundReport.Status.MATCHED)).filter(scope | date_window).exclude(pk=report.pk).order_by("-created_at")
    for other in candidates[:500]:
        lost,found=(report,other) if report.report_type==LostFoundReport.Type.LOST else (other,report)
        score=0; reasons=[]
        title=SequenceMatcher(None,lost.title.lower(),found.title.lower()).ratio()
        desc=SequenceMatcher(None,lost.description.lower(),found.description.lower()).ratio()
        if title>.45: score+=40; reasons.append("Similar item title")
        if desc>.35: score+=20; reasons.append("Similar description")
        if lost.colour and found.colour and lost.colour.lower()==found.colour.lower(): score+=20; reasons.append("Same colour")
        if lost.category_id and lost.category_id==found.category_id: score+=10; reasons.append("Same category")
        if lost.location.lower()==found.location.lower(): score+=10; reasons.append("Same location")
        if abs((lost.incident_date-found.incident_date).days) <= 30:
            score += 10; reasons.append("Reported within 30 days")
        if score < 40:
            existing = ItemMatch.objects.filter(lost_report=lost, found_report=found, is_reviewed=False).first()
            if existing and not existing.is_dismissed:
                existing.is_dismissed = True
                existing.save(update_fields=("is_dismissed",))
            continue
        match, created = ItemMatch.objects.get_or_create(
            lost_report=lost,
            found_report=found,
            defaults={"score":min(score,100),"reasons":reasons},
        )
        if not created and not match.is_reviewed and not match.is_dismissed:
            match.score = min(score, 100)
            match.reasons = reasons
            match.save(update_fields=("score", "reasons"))
        if created:
            LostFoundReport.objects.filter(pk__in=(lost.pk, found.pk)).update(status=LostFoundReport.Status.MATCHED)
            notify_possible_match(match)

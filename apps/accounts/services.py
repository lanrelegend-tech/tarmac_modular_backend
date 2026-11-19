import hashlib
import logging
import os
import secrets
from datetime import timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from .models import AccountActionToken

logger = logging.getLogger(__name__)


def issue_account_action_token(user, purpose):
    now = timezone.now()
    AccountActionToken.objects.filter(user=user, purpose=purpose, used_at__isnull=True).update(used_at=now)
    raw_token = secrets.token_urlsafe(32)
    lifetime = timedelta(hours=24) if purpose == AccountActionToken.Purpose.EMAIL_VERIFY else timedelta(hours=1)
    record = AccountActionToken.objects.create(
        user=user,
        purpose=purpose,
        token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
        expires_at=now + lifetime,
    )
    path = os.getenv("EMAIL_VERIFICATION_URL" if purpose == AccountActionToken.Purpose.EMAIL_VERIFY else "PASSWORD_RESET_URL", "")
    if not path:
        path = f"{os.getenv('FRONTEND_ORIGIN', 'http://localhost:3000')}/{'verify-email' if purpose == AccountActionToken.Purpose.EMAIL_VERIFY else 'reset-password'}"
    link = f"{path}?{urlencode({'email': user.email, 'token': raw_token})}"
    subject = "Verify your Tarmac email" if purpose == AccountActionToken.Purpose.EMAIL_VERIFY else "Reset your Tarmac password"
    action = "verify your email address" if purpose == AccountActionToken.Purpose.EMAIL_VERIFY else "reset your password"
    try:
        send_mail(subject, f"Use this one-time link to {action}:\n\n{link}\n\nThe link expires in {int(lifetime.total_seconds() // 3600)} hour(s).", settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=False)
    except Exception:
        logger.exception("Could not send account email for user %s", user.pk)
    return record


@transaction.atomic
def consume_account_action_token(email, raw_token, purpose):
    digest = hashlib.sha256(raw_token.encode()).hexdigest()
    try:
        token = AccountActionToken.objects.select_for_update().select_related("user").get(
            user__email__iexact=email.strip(), token_hash=digest, purpose=purpose,
            used_at__isnull=True, expires_at__gt=timezone.now(),
        )
    except AccountActionToken.DoesNotExist:
        return None
    token.used_at = timezone.now()
    token.save(update_fields=("used_at",))
    return token.user

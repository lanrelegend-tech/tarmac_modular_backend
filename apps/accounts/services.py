import hashlib
import logging
import os
import secrets
from datetime import timedelta
from urllib.parse import urlencode

from django.db import transaction
from django.utils import timezone

from .models import AccountActionToken
from apps.notifications.email import send_tarmac_email

logger = logging.getLogger(__name__)


def delete_verification_id_image(verification):
    """Delete the temporary ID upload from its backing storage and clear the field."""
    image = verification.student_id_image
    if not image:
        return False
    image.delete(save=False)
    verification.student_id_image = None
    return True


def save_account_profile_photo(user, uploaded_photo):
    old_name = user.profile_picture.name if user.profile_picture else None
    old_storage = user.profile_picture.storage if user.profile_picture else None
    user.profile_picture.save(uploaded_photo.name, uploaded_photo, save=False)
    user.save(update_fields=("profile_picture",))
    if old_name and old_storage and old_name != user.profile_picture.name:
        old_storage.delete(old_name)


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
    purpose_settings = {
        AccountActionToken.Purpose.EMAIL_VERIFY: ("EMAIL_VERIFICATION_URL", "verify-email"),
        AccountActionToken.Purpose.PASSWORD_RESET: ("PASSWORD_RESET_URL", "reset-password"),
        AccountActionToken.Purpose.ACCOUNT_DELETE: ("ACCOUNT_DELETE_URL", "delete-account"),
    }
    environment_key, default_page = purpose_settings.get(purpose, ("PASSWORD_RESET_URL", "reset-password"))
    path = os.getenv(environment_key, "")
    if not path:
        path = f"{os.getenv('FRONTEND_ORIGIN', 'http://localhost:3000').rstrip('/')}/{default_page}"
    link = f"{path}?{urlencode({'email': user.email, 'token': raw_token})}"
    messages = {
        AccountActionToken.Purpose.EMAIL_VERIFY: ("Verify your Tarmac email", "verify your email address", "Verify email"),
        AccountActionToken.Purpose.PASSWORD_RESET: ("Reset your Tarmac password", "reset your password", "Reset password"),
        AccountActionToken.Purpose.ACCOUNT_DELETE: ("Confirm deletion of your Tarmac account", "confirm deletion of your Tarmac account", "Confirm account deletion"),
    }
    subject, action, action_label = messages.get(purpose, messages[AccountActionToken.Purpose.PASSWORD_RESET])
    try:
        text_body = f"Hi {user.first_name or user.username},\n\nUse the button below to {action}. This secure link expires in {int(lifetime.total_seconds() // 3600)} hour(s).\n\nIf you did not request this, ignore this email.\n\n{link}"
        send_tarmac_email(
            subject=subject,
            text_body=text_body,
            recipient=user.email,
            recipient_name=user.first_name or user.username,
            action_url=link,
            action_label=action_label,
            preheader=f"Use this secure link to {action}.",
        )
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

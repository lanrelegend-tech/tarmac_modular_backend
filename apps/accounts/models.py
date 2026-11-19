from django.contrib.auth.models import AbstractUser
from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible
from django.db import models

@deconstructible
class PrivateStudentIDStorage(FileSystemStorage):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("location", settings.PRIVATE_MEDIA_ROOT)
        super().__init__(*args, **kwargs)
class User(AbstractUser):
    class Role(models.TextChoices): STUDENT="student", "Student"; ADMIN="admin", "Admin"; SUPER_ADMIN="super_admin", "Super admin"
    email = models.EmailField(unique=True)
    matric_number = models.CharField(max_length=40, unique=True, null=True, blank=True)
    department = models.CharField(max_length=120, blank=True); faculty = models.CharField(max_length=120, blank=True)
    level = models.CharField(max_length=30, blank=True); phone_number = models.CharField(max_length=25, blank=True)
    whatsapp_number = models.CharField(max_length=25, blank=True)
    profile_picture = models.ImageField(upload_to="profiles/", blank=True, null=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.STUDENT)
    is_student_verified = models.BooleanField(default=False); is_suspended = models.BooleanField(default=False)
    email_verified = models.BooleanField(default=False)
    notification_email_enabled = models.BooleanField(default=True); notification_whatsapp_enabled = models.BooleanField(default=False)
    whatsapp_consent_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        indexes = [models.Index(fields=("role", "date_joined"), name="user_role_joined_idx"), models.Index(fields=("is_student_verified", "is_suspended"), name="user_verification_active_idx")]
    USERNAME_FIELD = "email"; REQUIRED_FIELDS = ["username"]
    def __str__(self): return self.email
class StudentVerification(models.Model):
    class Status(models.TextChoices): PENDING="pending", "Pending"; APPROVED="approved", "Approved"; REJECTED="rejected", "Rejected"
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="verifications")
    student_id_image = models.ImageField(upload_to="student_ids/", storage=PrivateStudentIDStorage()); submitted_matric_number = models.CharField(max_length=40)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    reviewed_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="verification_reviews")
    reviewer_note = models.TextField(blank=True); created_at = models.DateTimeField(auto_now_add=True); reviewed_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("user",), condition=models.Q(status="pending"), name="one_pending_verification_per_user"),
            models.UniqueConstraint(fields=("submitted_matric_number",), condition=models.Q(status__in=("pending", "approved")), name="unique_active_student_matric"),
        ]

class AccountActionToken(models.Model):
    class Purpose(models.TextChoices): EMAIL_VERIFY="email_verify", "Email verification"; PASSWORD_RESET="password_reset", "Password reset"
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="action_tokens")
    purpose = models.CharField(max_length=20, choices=Purpose.choices)
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=("user", "purpose", "expires_at"), name="account_token_lookup_idx")]

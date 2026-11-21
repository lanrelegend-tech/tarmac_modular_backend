from django.contrib.auth.models import AbstractUser
from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone
from datetime import date
from uuid import uuid4
from config.storage import PrivateCloudinaryImageStorage

@deconstructible
class PrivateStudentIDStorage(FileSystemStorage):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("location", settings.PRIVATE_MEDIA_ROOT)
        super().__init__(*args, **kwargs)
class User(AbstractUser):
    class Role(models.TextChoices): STUDENT="student", "Student"; ADMIN="admin", "Admin"; SUPER_ADMIN="super_admin", "Super admin"
    email = models.EmailField(unique=True)
    matric_number = models.CharField(max_length=40, unique=True, null=True, blank=True)
    public_id = models.UUIDField(default=uuid4, unique=True, editable=False, db_index=True)
    public_name_enabled = models.BooleanField(default=False)
    public_department_enabled = models.BooleanField(default=False)
    public_level_enabled = models.BooleanField(default=False)
    public_profile_picture_enabled = models.BooleanField(default=False)
    public_profile_enabled = models.BooleanField(default=True)
    verified_affiliation = models.CharField(max_length=12, choices=(('student','Student'),('alumni','Alumni')), null=True, blank=True)
    student_status_override = models.CharField(max_length=12, choices=(('student','Student'),('alumni','Alumni')), null=True, blank=True)
    course = models.ForeignKey("accounts.CourseProgram", null=True, blank=True, on_delete=models.PROTECT, related_name="students")
    entry_year = models.PositiveSmallIntegerField(null=True, blank=True)
    department = models.CharField(max_length=120, blank=True); faculty = models.CharField(max_length=120, blank=True)
    level = models.CharField(max_length=30, blank=True); phone_number = models.CharField(max_length=25, blank=True)
    whatsapp_number = models.CharField(max_length=25, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True, db_index=True)
    campus = models.CharField(max_length=120, blank=True); bio = models.CharField(max_length=200, blank=True)
    profile_picture = models.ImageField(upload_to="profiles/", blank=True, null=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.STUDENT)
    is_student_verified = models.BooleanField(default=False); is_suspended = models.BooleanField(default=False)
    email_verified = models.BooleanField(default=False)
    notification_email_enabled = models.BooleanField(default=True); notification_whatsapp_enabled = models.BooleanField(default=False)
    whatsapp_consent_at = models.DateTimeField(null=True, blank=True)
    @property
    def student_status(self):
        if not self.is_student_verified:
            return "pending"
        if self.student_status_override:
            return self.student_status_override
        if self.verified_affiliation == "alumni":
            return "alumni"
        if self.verified_affiliation != "student" or not self.course_id or not self.entry_year:
            return "student"

        # A student finishes after completing the configured course duration
        # and the academic session ending in September of that graduation year.
        # The alumnus badge therefore takes effect on October 1.
        graduation_year = self.entry_year + self.course.duration_years
        if timezone.localdate() >= date(graduation_year, 10, 1):
            return "alumni"
        return "student"
    class Meta:
        indexes = [models.Index(fields=("role", "date_joined"), name="user_role_joined_idx"), models.Index(fields=("is_student_verified", "is_suspended"), name="user_verification_active_idx")]
        constraints = [models.UniqueConstraint(Lower("email"), name="user_email_casefold_uniq"), models.UniqueConstraint(Lower("username"), name="user_username_casefold_uniq")]
    USERNAME_FIELD = "email"; REQUIRED_FIELDS = ["username"]
    def __str__(self): return self.email


class CourseProgram(models.Model):
    name = models.CharField(max_length=160, unique=True)
    duration_years = models.PositiveSmallIntegerField(default=4)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)
        constraints = [models.CheckConstraint(condition=models.Q(duration_years__gte=1, duration_years__lte=10), name="course_duration_1_to_10_years")]

    def __str__(self):
        return self.name
class StudentVerification(models.Model):
    class Status(models.TextChoices): PENDING="pending", "Pending"; APPROVED="approved", "Approved"; REJECTED="rejected", "Rejected"
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="verifications")
    requested_affiliation = models.CharField(max_length=12, choices=(('student','Student'),('alumni','Alumni')), default='student')
    student_id_image = models.ImageField(upload_to="student_ids/", storage=PrivateCloudinaryImageStorage(), blank=True, null=True); submitted_matric_number = models.CharField(max_length=40)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    reviewed_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="verification_reviews")
    reviewer_note = models.TextField(blank=True); created_at = models.DateTimeField(auto_now_add=True); reviewed_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("user",), condition=models.Q(status="pending"), name="one_pending_verification_per_user"),
            models.UniqueConstraint(Lower("submitted_matric_number"), condition=models.Q(status__in=("pending", "approved")), name="unique_active_student_matric"),
        ]


class AnonymousMarketplaceIdentity(models.Model):
    """A public marketplace persona. The user link is deliberately private."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="anonymous_marketplace_identity")
    public_id = models.UUIDField(default=uuid4, unique=True, editable=False, db_index=True)
    handle = models.CharField(max_length=32, unique=True)
    is_enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.handle

class AccountActionToken(models.Model):
    class Purpose(models.TextChoices): EMAIL_VERIFY="email_verify", "Email verification"; PASSWORD_RESET="password_reset", "Password reset"; SUSPENSION_APPEAL="suspension_appeal", "Suspension appeal"
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="action_tokens")
    purpose = models.CharField(max_length=20, choices=Purpose.choices)
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=("user", "purpose", "expires_at"), name="account_token_lookup_idx")]


class ProfileChangeRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending review"
        NEEDS_INFO = "needs_info", "Waiting for student"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile_change_requests")
    requested_changes = models.JSONField(default=dict)
    requested_profile_picture = models.ImageField(upload_to="profile_change_requests/", storage=PrivateCloudinaryImageStorage(), blank=True, null=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="profile_change_reviews")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [models.UniqueConstraint(fields=("user",), condition=models.Q(status__in=("pending", "needs_info")), name="one_open_profile_change_request")]
        indexes = [models.Index(fields=("status", "created_at"), name="profile_change_queue_idx")]


class ProfileChangeMessage(models.Model):
    request = models.ForeignKey(ProfileChangeRequest, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="profile_change_messages")
    body = models.TextField(max_length=3000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at",)

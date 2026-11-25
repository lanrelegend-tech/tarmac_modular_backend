from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import UserChangeForm, UserCreationForm
from django import forms
from django.utils import timezone
from django.utils.html import format_html
from django.urls import reverse
from .models import User, StudentVerification, AnonymousMarketplaceIdentity, CourseProgram

class SchoolUserCreationForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("email", "username")

class SchoolUserChangeForm(UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = User
        fields = "__all__"

@admin.register(User)
class SchoolUserAdmin(UserAdmin):
    add_form = SchoolUserCreationForm
    form = SchoolUserChangeForm
    fieldsets = UserAdmin.fieldsets + (
        ("School profile", {"fields": ("matric_number", "department", "faculty", "level", "course", "entry_year", "verified_affiliation", "phone_number", "whatsapp_number", "profile_picture")}),
        ("Public privacy settings", {"fields": ("public_name_enabled", "public_department_enabled", "public_level_enabled", "public_profile_enabled", "public_profile_picture_enabled")}),
        ("Student access", {"fields": ("role", "email_verified", "is_student_verified", "is_suspended")}),
        ("Notifications", {"fields": ("notification_email_enabled", "notification_whatsapp_enabled", "whatsapp_consent_at")}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("email", "username", "password1", "password2")}),)
    list_display = ("email", "username", "matric_number", "is_student_verified", "is_suspended", "is_staff")
    search_fields = ("email", "username", "matric_number")
    ordering = ("email",)
    def has_delete_permission(self, request, obj=None):
        return False

@admin.register(StudentVerification)
class StudentVerificationAdmin(admin.ModelAdmin):
    list_display = ("user", "submitted_matric_number", "status", "created_at", "reviewed_by", "reviewed_at")
    list_filter = ("status", "created_at")
    search_fields = ("user__email", "submitted_matric_number")
    readonly_fields = ("created_at", "reviewed_at", "student_id_image_preview")
    class VerificationReviewForm(forms.ModelForm):
        class Meta:
            model = StudentVerification
            fields = "__all__"

        def clean(self):
            data = super().clean()
            if data.get("status") == StudentVerification.Status.APPROVED:
                user = data.get("user")
                matric = data.get("submitted_matric_number")
                if user and matric and User.objects.exclude(pk=user.pk).filter(matric_number=matric).exists():
                    self.add_error("submitted_matric_number", "This matric number is already assigned to another account.")
                from apps.administration.models import PlatformSettings
                if user and PlatformSettings.get_solo().email_verification_required and not user.email_verified:
                    self.add_error("status", "The student must verify their email before student approval.")
            return data
    form = VerificationReviewForm

    def get_fields(self, request, obj=None):
        fields = ["user", "submitted_matric_number", "requested_affiliation", "status", "reviewer_note", "created_at", "reviewed_by", "reviewed_at"]
        fields.insert(2, "student_id_image_preview" if obj else "student_id_image")
        return fields

    @admin.display(description="Private student ID image")
    def student_id_image_preview(self, obj):
        if not obj or not obj.student_id_image:
            return "No image uploaded."
        url = reverse("student-id-image", kwargs={"verification_id": obj.pk})
        return format_html('<a href="{}" target="_blank" rel="noopener">View student ID image</a>', url)

    def save_model(self, request, obj, form, change):
        old_image = None
        if change:
            old_image = StudentVerification.objects.get(pk=obj.pk).student_id_image
        if obj.status != StudentVerification.Status.PENDING:
            obj.reviewed_by = request.user
            obj.reviewed_at = timezone.now()
        super().save_model(request, obj, form, change)
        if old_image and old_image.name and old_image.name != getattr(obj.student_id_image, "name", None):
            old_image.delete(save=False)
        if obj.status in (StudentVerification.Status.APPROVED, StudentVerification.Status.REJECTED) and obj.student_id_image:
            from .services import delete_verification_id_image
            delete_verification_id_image(obj)
            obj.save(update_fields=("student_id_image",))
        if obj.status == StudentVerification.Status.APPROVED:
            User.objects.filter(pk=obj.user_id).update(
                is_student_verified=True,
                matric_number=obj.submitted_matric_number,
                verified_affiliation=obj.requested_affiliation,
                student_status_override=None,
            )
        elif obj.status == StudentVerification.Status.REJECTED:
            User.objects.filter(pk=obj.user_id).update(is_student_verified=False, verified_affiliation=None, student_status_override=None)

    def delete_model(self, request, obj):
        if obj.student_id_image:
            from .services import delete_verification_id_image
            delete_verification_id_image(obj)
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        from .services import delete_verification_id_image
        for obj in queryset:
            if obj.student_id_image:
                delete_verification_id_image(obj)
        super().delete_queryset(request, queryset)


@admin.register(AnonymousMarketplaceIdentity)
class AnonymousMarketplaceIdentityAdmin(admin.ModelAdmin):
    list_display = ("handle", "user", "is_enabled", "created_at")
    list_filter = ("is_enabled", "created_at")
    search_fields = ("handle", "user__email", "user__username", "user__matric_number")
    readonly_fields = ("public_id", "created_at", "updated_at")


@admin.register(CourseProgram)
class CourseProgramAdmin(admin.ModelAdmin):
    list_display = ("faculty", "name", "duration_years", "is_active")
    list_filter = ("is_active",)
    search_fields = ("faculty", "name")

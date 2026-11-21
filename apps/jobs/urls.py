from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    AdminJobListingViewSet, AdminJobReportViewSet, ExternalJobEmailConfirmView, ExternalJobResendEmailView,
    ExternalJobManageView, ExternalJobSubmissionView, JobCategoryViewSet,
    JobEngagementViewSet, JobListingViewSet, JobReportViewSet,
)

router = DefaultRouter()
router.register("categories", JobCategoryViewSet, basename="job-category")
router.register("listings", JobListingViewSet, basename="job-listing")
router.register("reports", JobReportViewSet, basename="job-report")
router.register("engagements", JobEngagementViewSet, basename="job-engagement")
admin_router = DefaultRouter()
admin_router.register("listings", AdminJobListingViewSet, basename="admin-job-listing")
admin_router.register("reports", AdminJobReportViewSet, basename="admin-job-report")

urlpatterns = [
    path("external/submit/", ExternalJobSubmissionView.as_view(), name="external-job-submit"),
    path("external/confirm-email/", ExternalJobEmailConfirmView.as_view(), name="external-job-confirm"),
    path("external/resend-email/", ExternalJobResendEmailView.as_view(), name="external-job-resend-email"),
    path("external/manage/", ExternalJobManageView.as_view(), name="external-job-manage"),
    path("admin/", include(admin_router.urls)),
    path("", include(router.urls)),
]

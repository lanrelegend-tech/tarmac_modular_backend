from rest_framework.routers import DefaultRouter
from django.urls import include,path
from .views import UserReportViewSet,AnnouncementViewSet,AdminLogViewSet,AdminVerificationViewSet,AdminReviewViewSet,AdminAccountViewSet,dashboard,send_admin_message,report_evidence_image
r=DefaultRouter(); r.register("reports",UserReportViewSet,basename="userreport"); r.register("announcements",AnnouncementViewSet,basename="announcement"); r.register("logs",AdminLogViewSet); r.register("verifications",AdminVerificationViewSet,basename="admin-verification"); r.register("reviews",AdminReviewViewSet,basename="admin-review"); r.register("users",AdminAccountViewSet,basename="admin-user")
urlpatterns=[path("dashboard/",dashboard,name="admin-dashboard"),path("messages/",send_admin_message,name="admin-message"),path("report-evidence/<int:evidence_id>/image/",report_evidence_image,name="report-evidence-image"),path("",include(r.urls))]

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import AdCampaignViewSet

router = DefaultRouter()
router.register("", AdCampaignViewSet, basename="ad-campaign")
urlpatterns = [path("", include(router.urls))]

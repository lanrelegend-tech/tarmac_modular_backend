from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import AdCampaignViewSet, AdPlacementViewSet

router = DefaultRouter()
router.register("placements", AdPlacementViewSet, basename="ad-placement")
router.register("", AdCampaignViewSet, basename="ad-campaign")
urlpatterns = [path("", include(router.urls))]

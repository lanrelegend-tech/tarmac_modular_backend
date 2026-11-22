from rest_framework.routers import DefaultRouter

from .views import AccommodationListingViewSet, HostelAgentRequestViewSet

router = DefaultRouter()
router.register("listings", AccommodationListingViewSet, basename="accommodation-listing")
router.register("agent-requests", HostelAgentRequestViewSet, basename="hostel-agent-request")

urlpatterns = router.urls

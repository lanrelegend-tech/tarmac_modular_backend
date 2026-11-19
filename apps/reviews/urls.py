from rest_framework.routers import DefaultRouter

from .views import SellerReviewViewSet

router = DefaultRouter()
router.register("", SellerReviewViewSet, basename="seller-review")

urlpatterns = router.urls

from rest_framework.routers import DefaultRouter

from .views import ReviewReportViewSet, SellerReviewViewSet

router = DefaultRouter()
router.register("reports", ReviewReportViewSet, basename="review-report")
router.register("", SellerReviewViewSet, basename="seller-review")

urlpatterns = router.urls

from rest_framework.routers import DefaultRouter
from django.urls import include,path
from .views import PaymentViewSet
r=DefaultRouter(); r.register("",PaymentViewSet,basename="payment")
urlpatterns=[path("",include(r.urls))]

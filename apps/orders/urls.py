from rest_framework.routers import DefaultRouter
from django.urls import include,path
from .views import OrderViewSet
r=DefaultRouter(); r.register("",OrderViewSet,basename="order")
urlpatterns=[path("",include(r.urls))]

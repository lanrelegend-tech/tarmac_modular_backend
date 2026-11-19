from rest_framework.routers import DefaultRouter
from django.urls import include,path
from .views import NotificationViewSet
r=DefaultRouter(); r.register("",NotificationViewSet,basename="notification")
urlpatterns=[path("",include(r.urls))]

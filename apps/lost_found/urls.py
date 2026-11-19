from rest_framework.routers import DefaultRouter
from django.urls import include,path
from .views import LostFoundReportViewSet,ItemMatchViewSet
r=DefaultRouter(); r.register("reports",LostFoundReportViewSet,basename="lostfound"); r.register("matches",ItemMatchViewSet,basename="itemmatch")
urlpatterns=[path("",include(r.urls))]

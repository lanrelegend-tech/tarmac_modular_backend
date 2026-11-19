from rest_framework.routers import DefaultRouter
from django.urls import include,path
from .views import ConversationViewSet,MessageViewSet
r=DefaultRouter(); r.register("conversations",ConversationViewSet,basename="conversation"); r.register("messages",MessageViewSet,basename="message")
urlpatterns=[path("",include(r.urls))]

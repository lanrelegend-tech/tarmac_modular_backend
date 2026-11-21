from rest_framework.routers import DefaultRouter
from django.urls import include,path
from .views import ConversationViewSet,MessageViewSet,BlockedUserViewSet,message_attachment_file
r=DefaultRouter(); r.register("conversations",ConversationViewSet,basename="conversation"); r.register("messages",MessageViewSet,basename="message"); r.register("blocks",BlockedUserViewSet,basename="block")
urlpatterns=[path("attachments/<int:attachment_id>/file/", message_attachment_file, name="message-attachment-file"),path("",include(r.urls))]

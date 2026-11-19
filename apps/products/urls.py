from django.urls import include,path
from rest_framework.routers import DefaultRouter
from .views import CategoryViewSet,ProductViewSet,ProductImageViewSet,FavouriteViewSet
r=DefaultRouter(); r.register("categories",CategoryViewSet); r.register("images",ProductImageViewSet,basename="product-image"); r.register("favourites",FavouriteViewSet,basename="favourite"); r.register("",ProductViewSet,basename="product")
urlpatterns=[path("",include(r.urls))]

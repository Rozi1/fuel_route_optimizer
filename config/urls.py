from django.contrib import admin
from django.urls import include, path

from routing.views import planner_page

urlpatterns = [
    path("", planner_page, name="home"),
    path("admin/", admin.site.urls),
    path("api/", include("routing.urls")),
]

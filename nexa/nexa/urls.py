from django.contrib import admin
from django.contrib.auth.views import LogoutView
from django.urls import path

from . import views
from .admin import check_integrations

urlpatterns = [
    path("", views.chat, name="chat"),
    path("c/<int:pk>/", views.chat, name="conversation"),
    path("stream/", views.stream_answer, name="stream"),
    path("c/<int:pk>/delete/", views.delete_conversation, name="delete-conversation"),
    path("login/", views.Login.as_view(), name="login"),
    path("logout/", LogoutView.as_view(next_page="login"), name="logout"),
    path("health/", views.health, name="health"),
    path("admin/integrations/check/", admin.site.admin_view(check_integrations), name="check-integrations"),
    path("admin/", admin.site.urls),
]

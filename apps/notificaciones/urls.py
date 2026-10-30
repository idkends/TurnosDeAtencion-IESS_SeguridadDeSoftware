from django.urls import path

from . import vistas

app_name = "notificaciones"

urlpatterns = [
    path("enlaces/<token:token>", vistas.enlace, name="enlace"),
]

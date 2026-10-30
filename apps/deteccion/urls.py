from django.urls import path

from . import vistas

app_name = "deteccion"

urlpatterns = [
    path("seguridad/alertas", vistas.alertas, name="alertas"),
    path("seguridad/alertas/<uuid:alerta_id>/reconocer", vistas.reconocer, name="reconocer"),
]

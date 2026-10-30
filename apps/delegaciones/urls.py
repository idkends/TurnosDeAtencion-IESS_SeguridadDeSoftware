from django.urls import path

from config.endpoints import despachar

from . import vistas

app_name = "delegaciones"

urlpatterns = [
    path("delegaciones", despachar(GET=vistas.lista, POST=vistas.crear), name="lista"),
    path("delegaciones/<uuid:delegacion_id>", vistas.revocar, name="revocar"),
]

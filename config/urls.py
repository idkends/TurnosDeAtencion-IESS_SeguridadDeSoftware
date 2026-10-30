from django.urls import include, path

from config import vistas

handler403 = "config.vistas.error_403"
handler404 = "config.vistas.error_404"
handler500 = "config.vistas.error_500"

urlpatterns = [
    path("", vistas.inicio, name="inicio"),
    path("salud/", vistas.salud, name="salud"),
    path("cuenta/", include("apps.cuentas.urls")),
]

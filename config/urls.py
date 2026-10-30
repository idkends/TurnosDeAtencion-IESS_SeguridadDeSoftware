from django.urls import include, path

from config import endpoints, vistas

endpoints.registrar_convertidores()

handler403 = "config.vistas.error_403"
handler404 = "config.vistas.error_404"
handler500 = "config.vistas.error_500"

urlpatterns = [
    path("", vistas.inicio, name="inicio"),
    path("salud", vistas.salud, name="salud"),
    path("", include("apps.cuentas.urls")),
    path("", include("apps.turnos.urls")),
    path("", include("apps.delegaciones.urls")),
    path("", include("apps.notificaciones.urls")),
]

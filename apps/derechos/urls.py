from django.urls import path

from config.endpoints import despachar

from . import vistas

app_name = "derechos"

urlpatterns = [
    path("mis-datos/exportar", vistas.exportar_mis_datos, name="exportar_mis_datos"),  # S13
    path("derechos/solicitudes", vistas.solicitudes, name="solicitudes"),  # S14
    path("derechos/solicitudes/<uuid:solicitud_id>/atender", vistas.atender, name="atender"),  # S15
    path("exportaciones", despachar(GET=vistas.exportaciones, POST=vistas.solicitar), name="exportaciones"),  # S16 / S17
    path("exportaciones/<uuid:exportacion_id>/aprobar", vistas.aprobar, name="aprobar"),  # S18
    path("exportaciones/<uuid:exportacion_id>/descargar", vistas.descargar, name="descargar"),  # S19
]

from django.urls import path

from config.endpoints import despachar

from . import vistas

app_name = "turnos"

urlpatterns = [
    path("disponibilidad", vistas.disponibilidad, name="disponibilidad"),
    path("turnos", despachar(GET=vistas.lista, POST=vistas.reservar), name="turnos"),
    path("turnos/<uuid:turno_id>", vistas.detalle, name="detalle"),
    path("turnos/<uuid:turno_id>/confirmar", vistas.confirmar, name="confirmar"),
    path("turnos/<uuid:turno_id>/cancelar", vistas.cancelar, name="cancelar"),
    path("turnos/<uuid:turno_id>/reprogramar", vistas.reprogramar, name="reprogramar"),
    path("turnos/<uuid:turno_id>/apelar", vistas.apelar, name="apelar"),
    path("interno/turnos/<uuid:turno_id>/asistencia", vistas.asistencia, name="asistencia"),
    path("interno/agenda", vistas.agenda, name="agenda"),
    path("interno/afiliados/<cedula:cedula>", vistas.afiliado, name="afiliado"),
    path("interno/afiliados/<cedula:cedula>/emergencia", vistas.emergencia, name="emergencia"),  # S25
    path("oferta/franjas", despachar(GET=vistas.oferta, POST=vistas.crear_oferta), name="oferta"),  # S22 / S23
    path("oferta/franjas/<uuid:franja_id>/cerrar", vistas.cerrar_oferta, name="cerrar_oferta"),  # S24
]

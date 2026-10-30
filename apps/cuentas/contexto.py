"""Menú de navegación según la persona autenticada: las plantillas solo lo recorren (no deciden permisos)."""
from . import politicas


def menu(request):
    usuario = request.user
    items = []
    if politicas.puede_reservar_en_linea(usuario):
        items += [("turnos:disponibilidad", "Reservar"), ("turnos:turnos", "Mis turnos"), ("delegaciones:lista", "Delegaciones"), ("cuentas:solicitudes_derechos", "Mis datos")]
    if politicas.puede_ver_agenda_unidad(usuario, getattr(usuario, "establecimiento_id", None)):
        items.append(("turnos:agenda", "Agenda de la unidad"))
    if politicas.puede_ver_auditoria(usuario):
        items.append(("cuentas:auditoria", "Auditoría"))
    return {"menu": items}

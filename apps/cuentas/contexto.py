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
    if politicas.puede_gestionar_alertas(usuario):
        items.append(("deteccion:alertas", "Alertas"))
    if politicas.puede_atender_derechos(usuario):
        items.append(("derechos:solicitudes", "Derechos"))
    if politicas.puede_solicitar_exportacion(usuario) or politicas.puede_aprobar_exportacion(usuario, None):
        items.append(("derechos:exportaciones", "Exportaciones"))
    if politicas.puede_ver_ficha_tecnica(usuario):
        items.append(("cuentas:contacto", "Mi contacto"))
    if politicas.puede_gestionar_oferta(usuario, getattr(usuario, "establecimiento_id", None)):
        items.append(("turnos:oferta", "Oferta"))
    return {"menu": items}

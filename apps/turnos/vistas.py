"""Vistas de turnos: finas. Validan el esquema, llaman a UN servicio y responden. Sin reglas de negocio ni ORM.

Respuestas de error uniformes: 404 igual para recurso inexistente o ajeno (CA-07), 403 sin delegación (CA-01), 409 con
mensaje genérico para reglas de negocio, 429 por límites. Toda operación que cambia estado es POST (o DELETE) con CSRF.
"""
import uuid

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from apps.cuentas.servicios import LimiteExcedido
from config.endpoints import endpoint

from . import esquemas, servicios


def _error(request, error):
    """Traduce las excepciones del servicio a respuestas HTTP uniformes."""
    if isinstance(error, servicios.NoEncontrado):
        return render(request, "errores/404.html", status=404)
    if isinstance(error, servicios.AccesoDenegado):
        return render(request, "errores/403.html", status=403)
    if isinstance(error, LimiteExcedido):
        respuesta = render(request, "errores/429.html", status=429)
        respuesta["Retry-After"] = str(max(1, error.reintentar_en))
        return respuesta
    if isinstance(error, servicios.ReglaRechazada):
        return render(request, "turnos/rechazo.html", {"error": error}, status=409)
    if isinstance(error, servicios.ConflictoIdempotencia):
        return render(request, "turnos/rechazo.html", {"error": servicios.ReglaRechazada("IDEMPOTENCIA", "Esta clave de solicitud ya se usó con otra petición distinta. Recargue la página e intente de nuevo.")}, status=409)
    if isinstance(error, servicios.ParametroInvalido):
        return render(request, "turnos/rechazo.html", {"error": servicios.ReglaRechazada("PARAMETRO", "Algún parámetro no es válido.")}, status=400)
    raise error


def _invalido(request, form):
    return render(request, "turnos/rechazo.html", {"error": servicios.ReglaRechazada("ENTRADA_INVALIDA", "Los datos enviados no son válidos."), "form": form}, status=400)


def _a_detalle(turno_id):
    return redirect(reverse("turnos:detalle", args=[turno_id]))


# --- E01 ---------------------------------------------------------------------------------------------------------------------------------


@endpoint("E01")
def disponibilidad(request):
    form = esquemas.DisponibilidadForm(request.GET)
    if not form.is_valid():
        return _invalido(request, form)
    d = form.cleaned_data
    try:
        pantalla = servicios.pantalla_disponibilidad(
            request.user, especialidad=d["especialidad"] or None, establecimiento=d["establecimiento"] or None, fecha=d["fecha"],
            pagina=d["pagina"] or 1, tamano=d["tamano"],
        )
    except servicios.ParametroInvalido as error:
        return _error(request, error)
    # Una clave de idempotencia distinta por franja y por carga de página: un doble clic repite la MISMA clave.
    filas = [(f, uuid.uuid4().hex) for f in pantalla.pagina.franjas]
    return render(request, "turnos/disponibilidad.html", {"pantalla": pantalla, "filas": filas, "filtros": d})


# --- E06 / E08 -----------------------------------------------------------------------------------------------------------------------------


@endpoint("E06")
def lista(request):
    form = esquemas.ListaTurnosForm(request.GET)
    if not form.is_valid():
        return _invalido(request, form)
    d = form.cleaned_data
    try:
        pantalla = servicios.pantalla_turnos(request.user, afiliado_id=d["afiliado"], estado=d["estado"] or None, pagina=d["pagina"] or 1, tamano=d["tamano"])
    except (servicios.NoEncontrado, servicios.AccesoDenegado, servicios.ParametroInvalido) as error:
        return _error(request, error)
    return render(request, "turnos/lista.html", {"pantalla": pantalla})


@endpoint("E08")
def reservar(request):
    form = esquemas.ReservaForm(request.POST)
    if not form.is_valid():
        return _invalido(request, form)
    d = form.cleaned_data
    try:
        turno, creado = servicios.reservar(
            solicitante=request.user, franja_id=d["franja"], motivo=d["motivo"], clave_idempotencia=d["clave_idempotencia"], afiliado_id=d["afiliado"],
        )
    except (servicios.NoEncontrado, servicios.AccesoDenegado, LimiteExcedido, servicios.ReglaRechazada, servicios.ConflictoIdempotencia) as error:
        return _error(request, error)
    if creado:
        messages.success(request, "Su turno fue reservado. Confírmelo dentro de las próximas 24 horas." if turno.estado == "RESERVADO" else "Su solicitud está en proceso: la confirmación del servicio de agenda está pendiente.")
    else:
        messages.info(request, "Esta solicitud ya había sido registrada: se muestra el mismo turno.")
    return _a_detalle(turno.id)


# --- E07 y acciones sobre un turno -------------------------------------------------------------------------------------------------------


@endpoint("E07")
def detalle(request, turno_id):
    try:
        turno = servicios.detalle_turno(request.user, turno_id)
    except (servicios.NoEncontrado, servicios.AccesoDenegado) as error:
        return _error(request, error)
    return render(request, "turnos/detalle.html", {"turno": turno, "clave": uuid.uuid4().hex})


@endpoint("E09")
def confirmar(request, turno_id):
    form = esquemas.ConfirmarForm(request.POST)
    if not form.is_valid():
        return _invalido(request, form)
    try:
        turno = servicios.confirmar_turno(request.user, turno_id)
    except (servicios.NoEncontrado, servicios.ReglaRechazada) as error:
        return _error(request, error)
    messages.success(request, "Su turno quedó confirmado.")
    return _a_detalle(turno.id)


@endpoint("E10")
def cancelar(request, turno_id):
    form = esquemas.CancelarForm(request.POST)
    if not form.is_valid():
        return _invalido(request, form)
    try:
        if (form.cleaned_data["accion"] or "cancelar") == "deshacer":
            turno = servicios.deshacer_cancelacion(request.user, turno_id)
            messages.success(request, "Se deshizo la cancelación: su turno sigue activo.")
        else:
            turno = servicios.cancelar_turno(request.user, turno_id)
            messages.success(request, "Su turno fue cancelado. Si fue un error, puede deshacerlo durante unos minutos desde el detalle.")
    except (servicios.NoEncontrado, servicios.ReglaRechazada) as error:
        return _error(request, error)
    return _a_detalle(turno.id)


@endpoint("E11")
def reprogramar(request, turno_id):
    form = esquemas.ReprogramarForm(request.POST)
    if not form.is_valid():
        return _invalido(request, form)
    try:
        turno, _ = servicios.reprogramar_turno(request.user, turno_id, form.cleaned_data["franja"], form.cleaned_data["clave_idempotencia"])
    except (servicios.NoEncontrado, servicios.ReglaRechazada, servicios.ConflictoIdempotencia) as error:
        return _error(request, error)
    messages.success(request, "Su turno fue reprogramado. Deberá confirmarlo de nuevo.")
    return _a_detalle(turno.id)


@endpoint("E12")
def apelar(request, turno_id):
    form = esquemas.ApelarForm(request.POST)
    if not form.is_valid():
        return _invalido(request, form)
    try:
        servicios.apelar_restriccion(request.user, turno_id, form.cleaned_data["texto"])
    except (servicios.NoEncontrado, servicios.ReglaRechazada) as error:
        return _error(request, error)
    messages.success(request, "Recibimos su apelación. Una persona de su unidad la responderá en un máximo de 5 días hábiles.")
    return _a_detalle(turno_id)


# --- Lado interno (funcionarios con segundo factor) --------------------------------------------------------------------------------------


@endpoint("E16")
def asistencia(request, turno_id):
    form = esquemas.AsistenciaForm(request.POST)
    if not form.is_valid():
        return _invalido(request, form)
    d = form.cleaned_data
    try:
        servicios.registrar_atencion(request.user, turno_id, d["resultado"], justificacion_tipo=d["justificacion_tipo"])
    except (servicios.NoEncontrado, servicios.AccesoDenegado, servicios.ReglaRechazada, servicios.ParametroInvalido) as error:
        return _error(request, error)
    messages.success(request, "Registro guardado.")
    return redirect(reverse("turnos:agenda"))


@endpoint("E17")
def agenda(request):
    form = esquemas.AgendaForm(request.GET)
    if not form.is_valid():
        return _invalido(request, form)
    d = form.cleaned_data
    try:
        pagina = servicios.agenda_unidad(request.user, fecha=d["fecha"], pagina=d["pagina"] or 1, tamano=d["tamano"])
    except servicios.AccesoDenegado as error:
        return _error(request, error)
    return render(request, "turnos/agenda.html", {"agenda": pagina})


@endpoint("E18")
def afiliado(request, cedula):
    form = esquemas.ContactoForm(request.GET)
    if not form.is_valid():
        return _invalido(request, form)
    try:
        contacto = servicios.consultar_afiliado(request.user, cedula, form.cleaned_data["justificacion"])
    except (servicios.NoEncontrado, servicios.AccesoDenegado, LimiteExcedido) as error:
        return _error(request, error)
    return render(request, "turnos/afiliado.html", {"contacto": contacto, "cedula_url": cedula})

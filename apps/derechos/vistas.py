"""Vistas de derechos del titular, solicitudes del DPD y exportaciones puntuales. Finas: llaman a UN servicio."""
from datetime import datetime, time, timedelta

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone as zona
from django.utils.http import urlencode

from apps.cuentas.servicios import LimiteExcedido
from config.endpoints import endpoint

from . import esquemas, servicios


def _error(request, error):
    if isinstance(error, servicios.AccesoDenegado):
        return render(request, "errores/403.html", status=403)
    if isinstance(error, servicios.NoEncontrado):
        return render(request, "errores/404.html", status=404)
    if isinstance(error, servicios.DerechosRechazado):
        return render(request, "derechos/rechazo.html", {"mensaje": error.mensaje}, status=409)
    raise error


@endpoint("S13")
def exportar_mis_datos(request):
    try:
        contenido = servicios.exportar_mis_datos(request)
    except servicios.OperacionDiferida as error:
        return render(request, "cuentas/operacion_diferida.html", {"hasta": error.hasta}, status=423)
    except servicios.ReautenticacionRequerida as error:
        destino = "cuentas:mfa_configurar" if error.configurar_mfa else "cuentas:reautenticar"
        return redirect(f"{reverse(destino)}?{urlencode({'next': reverse('derechos:exportar_mis_datos')})}")
    except servicios.AccesoDenegado as error:
        return _error(request, error)
    respuesta = HttpResponse(contenido, content_type="application/json; charset=utf-8")
    respuesta["Content-Disposition"] = 'attachment; filename="mis-datos.json"'
    return respuesta


@endpoint("S14")
def solicitudes(request):
    form = esquemas.PaginaForm(request.GET)
    if not form.is_valid():
        return render(request, "errores/404.html", status=400)
    try:
        filas, total = servicios.listar_solicitudes(request.user, form.cleaned_data["pagina"] or 1)
    except servicios.AccesoDenegado as error:
        return _error(request, error)
    return render(request, "derechos/solicitudes.html", {"filas": filas, "total": total})


@endpoint("S15")
def atender(request, solicitud_id):
    if not esquemas.VacioForm(request.POST).is_valid():
        return render(request, "errores/404.html", status=400)
    try:
        resultado = servicios.atender_solicitud(request.user, solicitud_id)
    except (servicios.AccesoDenegado, servicios.NoEncontrado, servicios.DerechosRechazado) as error:
        return _error(request, error)
    messages.success(request, resultado)
    return redirect(reverse("derechos:solicitudes"))


@endpoint("S16")
def exportaciones(request):
    try:
        lista = servicios.listar_exportaciones(request.user)
    except servicios.AccesoDenegado as error:
        return _error(request, error)
    return render(request, "derechos/exportaciones.html", {"exportaciones": lista, "form": esquemas.ExportacionForm()})


@endpoint("S17")
def solicitar(request):
    form = esquemas.ExportacionForm(request.POST)
    if not form.is_valid():
        return render(request, "derechos/rechazo.html", {"mensaje": "Los datos enviados no son válidos."}, status=400)
    d = form.cleaned_data
    inicio = zona.make_aware(datetime.combine(d["desde"], time.min))
    fin = zona.make_aware(datetime.combine(d["hasta"], time.min)) + timedelta(days=1)
    try:
        servicios.solicitar_exportacion(request.user, motivo=d["motivo"], desde=inicio, hasta=fin)
    except (servicios.AccesoDenegado, servicios.DerechosRechazado) as error:
        return _error(request, error)
    messages.success(request, "Solicitud registrada. Una segunda persona debe aprobarla antes de poder descargarla.")
    return redirect(reverse("derechos:exportaciones"))


@endpoint("S18")
def aprobar(request, exportacion_id):
    if not esquemas.VacioForm(request.POST).is_valid():
        return render(request, "errores/404.html", status=400)
    try:
        servicios.aprobar_exportacion(request.user, exportacion_id)
    except (servicios.AccesoDenegado, servicios.NoEncontrado) as error:
        return _error(request, error)
    messages.success(request, "Exportación aprobada.")
    return redirect(reverse("derechos:exportaciones"))


@endpoint("S19")
def descargar(request, exportacion_id):
    try:
        contenido = servicios.entregar_exportacion(request.user, exportacion_id)
    except (servicios.AccesoDenegado, servicios.NoEncontrado, servicios.DerechosRechazado) as error:
        return _error(request, error)
    respuesta = HttpResponse(contenido, content_type="text/csv; charset=utf-8")
    respuesta["Content-Disposition"] = 'attachment; filename="auditoria.csv"'
    return respuesta

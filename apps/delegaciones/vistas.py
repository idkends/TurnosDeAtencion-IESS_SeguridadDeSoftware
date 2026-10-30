"""Vistas de delegaciones: finas. E13 crea (con segundo factor reciente) y E14 revoca; S10 lista."""
from django.contrib import messages
from django.http import HttpResponseNotAllowed
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import urlencode

from apps.cuentas.servicios import LimiteExcedido, OperacionDiferida, ReautenticacionRequerida
from config.endpoints import endpoint

from . import esquemas, servicios


def _error(request, error):
    if isinstance(error, servicios.NoEncontrado):
        return render(request, "errores/404.html", status=404)
    if isinstance(error, servicios.AccesoDenegado):
        return render(request, "errores/403.html", status=403)
    raise error


@endpoint("S10")
def lista(request):
    try:
        otorgadas, recibidas = servicios.listar_delegaciones(request.user)
    except servicios.AccesoDenegado as error:
        return _error(request, error)
    return render(request, "delegaciones/lista.html", {"otorgadas": otorgadas, "recibidas": recibidas, "form": esquemas.DelegacionForm()})


@endpoint("E13")
def crear(request):
    form = esquemas.DelegacionForm(request.POST)
    if not form.is_valid():
        return render(request, "delegaciones/rechazo.html", {"mensaje": "Los datos enviados no son válidos."}, status=400)
    d = form.cleaned_data
    try:
        if d["paso"] == "verificar":
            persona = servicios.previsualizar_representante(request, d["cedula"])
            return render(request, "delegaciones/confirmar.html", {"persona": persona, "cedula": d["cedula"]})
        servicios.crear_delegacion(request, d["cedula"])
    except OperacionDiferida as error:
        return render(request, "cuentas/operacion_diferida.html", {"hasta": error.hasta}, status=423)
    except ReautenticacionRequerida as error:
        destino = "cuentas:mfa_configurar" if error.configurar_mfa else "cuentas:reautenticar"
        return redirect(f"{reverse(destino)}?{urlencode({'next': reverse('delegaciones:lista')})}")
    except servicios.DelegacionRechazada:
        # Mensaje genérico: no revela si la cédula consultada tiene cuenta (R18).
        return render(request, "delegaciones/rechazo.html", {"mensaje": "No fue posible crear la delegación. Verifique la cédula e intente de nuevo."}, status=409)
    except (servicios.AccesoDenegado, LimiteExcedido) as error:
        return _error(request, error) if isinstance(error, servicios.AccesoDenegado) else render(request, "errores/429.html", status=429)
    messages.success(request, "Delegación creada. La persona puede ayudarle con su agenda durante 12 meses; usted puede revocarla cuando quiera.")
    return redirect(reverse("delegaciones:lista"))


@endpoint("E14")
def revocar(request, delegacion_id):
    # Los formularios HTML no envían DELETE: aceptan POST con _metodo=DELETE; cualquier otro POST es 405.
    if request.method == "POST" and request.POST.get("_metodo") != "DELETE":
        return HttpResponseNotAllowed(["DELETE"])
    form = esquemas.RevocarForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and not form.is_valid():
        return render(request, "delegaciones/rechazo.html", {"mensaje": "Los datos enviados no son válidos."}, status=400)
    try:
        servicios.revocar_delegacion(request.user, delegacion_id)
    except servicios.NoEncontrado as error:
        return _error(request, error)
    if request.method == "DELETE":
        return render(request, "delegaciones/revocada.html")
    messages.success(request, "La delegación fue revocada; surte efecto de inmediato.")
    return redirect(reverse("delegaciones:lista"))

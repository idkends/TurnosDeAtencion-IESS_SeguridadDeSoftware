"""Panel de alertas para el responsable de seguridad. Finas: validan, llaman a UN servicio y responden."""
from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse

from config.endpoints import endpoint

from . import esquemas, servicios


@endpoint("S11")
def alertas(request):
    form = esquemas.FiltroAlertasForm(request.GET)
    if not form.is_valid():
        return render(request, "errores/404.html", status=400)
    d = form.cleaned_data
    try:
        panel = servicios.listar_alertas(request.user, severidad=d["severidad"] or None, estado=d["estado"] or None, pagina=d["pagina"] or 1)
    except servicios.AccesoDenegado:
        return render(request, "errores/403.html", status=403)
    return render(request, "deteccion/alertas.html", {"panel": panel, "form": form})


@endpoint("S12")
def reconocer(request, alerta_id):
    form = esquemas.ReconocerForm(request.POST)
    if not form.is_valid():
        return render(request, "errores/404.html", status=400)
    try:
        servicios.reconocer_alerta(request.user, alerta_id, form.cleaned_data["nota"])
    except servicios.AccesoDenegado:
        return render(request, "errores/403.html", status=403)
    except servicios.NoEncontrado:
        return render(request, "errores/404.html", status=404)
    messages.success(request, "Alerta reconocida.")
    return redirect(reverse("deteccion:alertas"))

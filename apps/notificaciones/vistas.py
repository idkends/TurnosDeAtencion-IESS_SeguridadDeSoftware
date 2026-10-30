"""E20: confirmación desde un aviso. El token es de un solo uso y SOLO lleva a iniciar sesión: nunca abre datos."""
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import urlencode

from config.endpoints import endpoint

from . import servicios


@endpoint("E20")
def enlace(request, token):
    turno_id = servicios.consumir_enlace(token)
    if turno_id is None:
        return render(request, "notificaciones/enlace_invalido.html", status=410)
    # Redirige a iniciar sesión; el detalle del turno vuelve a comprobar la autorización por objeto.
    return redirect(f"{reverse('cuentas:ingresar')}?{urlencode({'next': f'/turnos/{turno_id}'})}")

"""Ayudas compartidas por las pruebas de turnos, delegaciones y endpoints (no son fixtures: funciones simples)."""
import re
import types
from datetime import datetime, time, timedelta

import pyotp
from django.urls import reverse
from django.utils import timezone as zona

from apps.integraciones import servicios as integraciones
from apps.integraciones.servicios import DatosAfiliacion
from apps.turnos.modelos import Especialidad, Establecimiento, Franja
from config import reloj

HTTPS = {"secure": True}
CLAVE = "Zorro-Azul-Lluvia-731"


def crear_oferta() -> dict:
    est1 = Establecimiento.objects.create(codigo="QUI01", nombre="Centro Norte")
    est2 = Establecimiento.objects.create(codigo="GUA01", nombre="Centro Sur")
    esp1 = Especialidad.objects.create(codigo="MGEN", nombre="Medicina general")
    esp2 = Especialidad.objects.create(codigo="PEDI", nombre="Pediatría")
    esp3 = Especialidad.objects.create(codigo="ODON", nombre="Odontología")
    return {"est1": est1, "est2": est2, "esp1": esp1, "esp2": esp2, "esp3": esp3}


def franja_futura(est, esp, *, dias=3, hora=9, minuto=0, duracion=30, prioritaria=False) -> Franja:
    """Franja libre a `dias` días de hoy (hora local). Cada combinación (est, esp, día, hora) es única."""
    dia = zona.localdate(reloj.ahora()) + timedelta(days=dias)
    inicio = zona.make_aware(datetime.combine(dia, time(hora, minuto)))
    return Franja.objects.create(establecimiento=est, especialidad=esp, inicio=inicio, fin=inicio + timedelta(minutes=duracion), cupo_prioritario=prioritaria)


def franja_en(est, esp, delta: timedelta, duracion=30) -> Franja:
    """Franja que empieza exactamente a `delta` de ahora (para probar la regla de 2 horas)."""
    inicio = reloj.ahora() + delta
    return Franja.objects.create(establecimiento=est, especialidad=esp, inicio=inicio, fin=inicio + timedelta(minutes=duracion))


def registrar_afiliacion(usuario, *, vigente=True):
    datos = DatosAfiliacion(cedula=usuario.cedula, nombres=usuario.nombres, apellidos=usuario.apellidos, correo=usuario.correo,
                            telefono=usuario.telefono, vigente=vigente, fecha_nacimiento=None)
    integraciones.afiliacion_memoria().agregar(datos)
    return datos


def peticion(usuario, *, mfa=True):
    """Solicitud mínima con sesión simulada, para los servicios que exigen segundo factor reciente."""
    ahora = reloj.ahora().timestamp()
    sesion = {"ultima_actividad": ahora, "mfa_en": ahora if mfa else None}
    if mfa:
        usuario.totp_confirmado = True
    return types.SimpleNamespace(user=usuario, session=types.SimpleNamespace(get=sesion.get), actividad_previa=ahora)


def iniciar_sesion(cliente, usuario, clave=CLAVE):
    return cliente.post(reverse("cuentas:ingresar"), {"cedula": usuario.cedula, "clave": clave}, **HTTPS)


def iniciar_sesion_con_mfa(cliente, usuario, clave=CLAVE) -> str:
    """Inicia sesión y configura el segundo factor (queda reciente). Devuelve el secreto TOTP."""
    r = iniciar_sesion(cliente, usuario, clave)
    if r["Location"].startswith(reverse("cuentas:mfa_configurar")):
        pagina = cliente.get(r["Location"], **HTTPS).content.decode()
    else:
        pagina = cliente.get(reverse("cuentas:mfa_configurar"), **HTTPS).content.decode()
    secreto = re.search(r"<strong>([A-Z2-7]{32})</strong>", pagina).group(1)
    cliente.post(reverse("cuentas:mfa_configurar"), {"codigo": pyotp.TOTP(secreto).at(int(reloj.ahora().timestamp()))}, **HTTPS)
    return secreto

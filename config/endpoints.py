"""Decorador de endpoints: ata cada vista a su entrada del inventario (Anexo D) y aplica lo que ahí se declara.

Qué hace (y solo eso): método permitido, sesión requerida y límites de solicitudes por IP y por cuenta (R21, API4). Nada de
reglas de negocio ni de permisos por objeto: eso vive en los servicios y en `cuentas.politicas`.
La auditoría de un límite excedido sale por la señal `limite_excedido` (la recibe `auditoria`): `config` no importa apps.
"""
import functools

from django.contrib.auth.views import redirect_to_login
from django.dispatch import Signal
from django.http import HttpResponseNotAllowed
from django.shortcuts import render
from django.urls import register_converter

from config import bloqueos, contexto, limites
from config.inventario import INVENTARIO
from config.parametros import P

limite_excedido = Signal()  # argumentos: ambito, identificador (resumen), usuario_id


class _ConvertidorCedula:
    regex = r"[0-9]{10}"

    def to_python(self, valor):
        return valor

    def to_url(self, valor):
        return str(valor)


class _ConvertidorToken:
    regex = r"[A-Za-z0-9_\-]{16,64}"

    def to_python(self, valor):
        return valor

    def to_url(self, valor):
        return str(valor)


def registrar_convertidores() -> None:
    register_converter(_ConvertidorCedula, "cedula")
    register_converter(_ConvertidorToken, "token")


def endpoint(id_endpoint: str):
    """`@endpoint("E08")`: la vista queda registrada con las reglas de su entrada en el inventario."""
    entrada = INVENTARIO[id_endpoint]

    def decorador(vista):
        @functools.wraps(vista)
        def envuelta(request, *args, **kwargs):
            if request.method not in entrada.metodos:
                return HttpResponseNotAllowed(entrada.metodos)
            autenticado = request.user.is_authenticated
            if entrada.requiere_sesion and not autenticado:
                return redirect_to_login(request.get_full_path())
            if entrada.sala_espera and bloqueos.sala_espera_activa():
                # Sala de espera simple: solo se admite un número fijo de solicitudes por segundo; el resto espera.
                if not limites.consumir("sala-espera", "global", P.sala_espera_admision_por_segundo, 1).permitido:
                    limite_excedido.send(sender=None, ambito=f"sala_espera:{id_endpoint}", usuario_id=request.user.pk if autenticado else None)
                    respuesta = render(request, "errores/sala_espera.html", status=503)
                    respuesta["Retry-After"] = "5"
                    return respuesta
            for ambito, por, parametro, ventana in entrada.limites:
                if por == "cuenta" and not autenticado:
                    continue
                if por == "dispositivo":
                    identificador = contexto.actual().dispositivo
                    if not identificador:
                        continue  # sin cookie de dispositivo no hay dimensión de dispositivo que limitar
                elif por == "cuenta":
                    identificador = str(request.user.pk)
                else:
                    identificador = contexto.actual().ip or "desconocida"
                resultado = limites.consumir(f"{ambito}:{id_endpoint}", identificador, getattr(P, parametro), ventana)
                if not resultado.permitido:
                    limite_excedido.send(sender=None, ambito=f"{ambito}:{id_endpoint}", usuario_id=request.user.pk if autenticado else None)
                    respuesta = render(request, "errores/429.html", status=429)
                    respuesta["Retry-After"] = str(max(1, resultado.reintentar_en))
                    return respuesta
            return vista(request, *args, **kwargs)

        envuelta.endpoint_id = id_endpoint
        envuelta.endpoint_metodos = entrada.metodos
        return envuelta

    return decorador


def despachar(**por_metodo):
    """Una misma ruta con endpoints distintos por método (por ejemplo GET /turnos = E06 y POST /turnos = E08)."""

    def vista(request, *args, **kwargs):
        destino = por_metodo.get(request.method)
        if destino is None:
            return HttpResponseNotAllowed(list(por_metodo))
        return destino(request, *args, **kwargs)

    vista.endpoints = {metodo: destino.endpoint_id for metodo, destino in por_metodo.items()}
    return vista

"""Cliente HTTP del servicio de afiliación. Aquí viven el tiempo de espera, los reintentos y el circuito abierto.

Solo usa la biblioteca estándar (urllib). Las respuestas se validan estrictamente: una respuesta mal formada se
trata como fallo del tercero, nunca como "no afiliado" ni como cobertura.
"""
import json
import time
import urllib.error
import urllib.request
from datetime import date

from config.cedula import es_valida
from config.parametros import P

from .. import servicio_token
from ..circuito import Circuito
from ..puertos import DatosAfiliacion, ServicioNoDisponible

_LIMITE_RESPUESTA = 64 * 1024
_CAMPOS = {"cedula", "nombres", "apellidos", "correo", "telefono", "vigente", "fecha_nacimiento"}


class _NoHayAfiliado(Exception):
    pass


def _validar_respuesta(cedula: str, cuerpo: dict) -> DatosAfiliacion:
    if not isinstance(cuerpo, dict) or set(cuerpo) != _CAMPOS:
        raise ServicioNoDisponible("esquema de respuesta inválido")
    if cuerpo["cedula"] != cedula or not es_valida(cuerpo["cedula"]):
        raise ServicioNoDisponible("la respuesta no corresponde a la cédula consultada")
    for campo in ("nombres", "apellidos", "correo", "telefono"):
        if not isinstance(cuerpo[campo], str) or len(cuerpo[campo]) > 200:
            raise ServicioNoDisponible(f"campo inválido: {campo}")
    if not isinstance(cuerpo["vigente"], bool):
        raise ServicioNoDisponible("campo inválido: vigente")
    nacimiento = None
    if cuerpo["fecha_nacimiento"] is not None:
        try:
            nacimiento = date.fromisoformat(cuerpo["fecha_nacimiento"])
        except (TypeError, ValueError):
            raise ServicioNoDisponible("campo inválido: fecha_nacimiento") from None
    return DatosAfiliacion(
        cedula=cuerpo["cedula"],
        nombres=cuerpo["nombres"],
        apellidos=cuerpo["apellidos"],
        correo=cuerpo["correo"],
        telefono=cuerpo["telefono"],
        vigente=cuerpo["vigente"],
        fecha_nacimiento=nacimiento,
    )


class AfiliacionHttp:
    def __init__(self, base_url: str, secreto: bytes, circuito: Circuito | None = None, dormir=time.sleep):
        self._base = base_url.rstrip("/")
        self._secreto = secreto
        self._dormir = dormir
        self.circuito = circuito or Circuito(P.circuito_fallos, P.circuito_ventana_segundos, P.circuito_pausa_segundos)

    def consultar(self, cedula: str) -> DatosAfiliacion | None:
        if not es_valida(cedula):
            return None  # no se envía al tercero algo que ni siquiera es una cédula
        if self.circuito.abierto():
            raise ServicioNoDisponible("circuito abierto")
        ruta = f"/afiliados/{cedula}"
        ultimo_error: Exception | None = None
        for intento in range(P.integracion_reintentos + 1):
            if intento:
                self._dormir(P.integracion_espera_base_segundos * 2 ** (intento - 1))  # espera creciente
            try:
                resultado = self._solicitar(ruta, cedula)
            except _NoHayAfiliado:
                self.circuito.registrar_exito()
                return None
            except (urllib.error.URLError, TimeoutError, OSError, ValueError, ServicioNoDisponible) as error:
                ultimo_error = error
                continue
            self.circuito.registrar_exito()
            return resultado
        self.circuito.registrar_fallo()
        raise ServicioNoDisponible(f"el servicio de afiliación no respondió: {type(ultimo_error).__name__}")

    def _solicitar(self, ruta: str, cedula: str) -> DatosAfiliacion:
        peticion = urllib.request.Request(
            self._base + ruta,
            headers={"X-Servicio-Token": servicio_token.firmar(self._secreto, ruta), "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(peticion, timeout=P.integracion_timeout_segundos) as respuesta:  # noqa: S310
                crudo = respuesta.read(_LIMITE_RESPUESTA + 1)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                raise _NoHayAfiliado from None
            raise
        if len(crudo) > _LIMITE_RESPUESTA:
            raise ServicioNoDisponible("respuesta demasiado grande")
        return _validar_respuesta(cedula, json.loads(crudo))

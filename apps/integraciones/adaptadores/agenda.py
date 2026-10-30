"""Sistema de agenda: adaptador en memoria (pruebas) y cliente HTTP con tiempo de espera, reintentos y circuito.

Al agenda solo se le envían identificadores internos (franja, turno y afiliado opacos): ni cédula ni nombres (§4.6).
Los reintentos son seguros porque `confirmar` lleva una clave de idempotencia estable.
"""
import json
import re
import time
import urllib.error
import urllib.request

from config.parametros import P

from .. import servicio_token
from ..circuito import Circuito
from ..puertos import ConfirmacionAgenda, ServicioNoDisponible

_LIMITE_RESPUESTA = 16 * 1024
_ESTADOS = {"CONFIRMADA", "RECHAZADA"}
_REF = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class AgendaMemoria:
    """Agenda de prueba: confirma por defecto, puede rechazar franjas concretas o caerse."""

    def __init__(self):
        self.limpiar()

    def limpiar(self):
        self.caida = False
        self.rechazar: set[str] = set()
        self.confirmaciones: dict[str, ConfirmacionAgenda] = {}
        self.llamadas = 0
        self.cancelados: list[str] = []

    def confirmar(self, *, turno_ref, franja_ref, afiliado_ref, clave) -> ConfirmacionAgenda:
        self.llamadas += 1
        if self.caida:
            raise ServicioNoDisponible("simulado: agenda caída")
        if clave in self.confirmaciones:  # idempotencia: el mismo intento devuelve lo mismo
            return self.confirmaciones[clave]
        estado = "RECHAZADA" if franja_ref in self.rechazar else "CONFIRMADA"
        self.confirmaciones[clave] = ConfirmacionAgenda(estado, f"AG-{len(self.confirmaciones) + 1:06d}")
        return self.confirmaciones[clave]

    def cancelar(self, *, turno_ref) -> None:
        if self.caida:
            raise ServicioNoDisponible("simulado: agenda caída")
        self.cancelados.append(turno_ref)


class AgendaHttp:
    def __init__(self, base_url: str, secreto: bytes, circuito: Circuito | None = None, dormir=time.sleep):
        self._base = base_url.rstrip("/")
        self._secreto = secreto
        self._dormir = dormir
        self.circuito = circuito or Circuito(P.circuito_fallos, P.circuito_ventana_segundos, P.circuito_pausa_segundos)

    def confirmar(self, *, turno_ref, franja_ref, afiliado_ref, clave) -> ConfirmacionAgenda:
        cuerpo = {"turno_ref": turno_ref, "franja_ref": franja_ref, "afiliado_ref": afiliado_ref, "clave": clave}
        datos = self._con_reintentos("POST", "/reservas", cuerpo)
        if not isinstance(datos, dict) or set(datos) != {"estado", "referencia"} or datos["estado"] not in _ESTADOS or not isinstance(datos["referencia"], str) or len(datos["referencia"]) > 64:
            raise ServicioNoDisponible("esquema de respuesta de agenda inválido")
        return ConfirmacionAgenda(datos["estado"], datos["referencia"])

    def cancelar(self, *, turno_ref) -> None:
        if not _REF.match(turno_ref):
            raise ServicioNoDisponible("referencia inválida")
        self._con_reintentos("DELETE", f"/reservas/{turno_ref}", None)

    def _con_reintentos(self, metodo: str, ruta: str, cuerpo: dict | None):
        if self.circuito.abierto():
            raise ServicioNoDisponible("circuito abierto")
        ultimo: Exception | None = None
        for intento in range(P.integracion_reintentos + 1):
            if intento:
                self._dormir(P.integracion_espera_base_segundos * 2 ** (intento - 1))
            try:
                resultado = self._solicitar(metodo, ruta, cuerpo)
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
                ultimo = error
                continue
            self.circuito.registrar_exito()
            return resultado
        self.circuito.registrar_fallo()
        raise ServicioNoDisponible(f"la agenda no respondió: {type(ultimo).__name__}")

    def _solicitar(self, metodo: str, ruta: str, cuerpo: dict | None):
        crudo_envio = json.dumps(cuerpo).encode() if cuerpo is not None else None
        peticion = urllib.request.Request(
            self._base + ruta, data=crudo_envio, method=metodo,
            headers={"X-Servicio-Token": servicio_token.firmar(self._secreto, ruta), "Content-Type": "application/json", "Accept": "application/json"},
        )
        with urllib.request.urlopen(peticion, timeout=P.integracion_timeout_segundos) as respuesta:  # noqa: S310  # nosec B310
            crudo = respuesta.read(_LIMITE_RESPUESTA + 1)
        if len(crudo) > _LIMITE_RESPUESTA:
            raise ValueError("respuesta demasiado grande")
        return json.loads(crudo) if crudo else {}

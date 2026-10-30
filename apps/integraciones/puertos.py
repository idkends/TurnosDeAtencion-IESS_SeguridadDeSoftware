"""Puertos (interfaces) de los terceros. El dominio depende de esto, nunca de un cliente HTTP."""
from dataclasses import dataclass
from datetime import date
from typing import Protocol


class ServicioNoDisponible(Exception):
    """El tercero no respondió bien (tiempo agotado, error, respuesta inválida o circuito abierto).

    Nunca significa "no afiliado": quien llama debe dejar la operación en estado pendiente y no afirmar cobertura.
    """


@dataclass(frozen=True)
class DatosAfiliacion:
    cedula: str
    nombres: str
    apellidos: str
    correo: str
    telefono: str
    vigente: bool
    # La fecha de nacimiento solo se usa para rechazar claves biográficas; no se persiste ni se guarda en caché.
    fecha_nacimiento: date | None = None


class PuertoAfiliacion(Protocol):
    def consultar(self, cedula: str) -> DatosAfiliacion | None:
        """Devuelve los datos, o None si la cédula no consta como afiliada. Lanza ServicioNoDisponible si falla."""


@dataclass(frozen=True)
class ConfirmacionAgenda:
    estado: str  # CONFIRMADA | RECHAZADA
    referencia: str


class PuertoAgenda(Protocol):
    def confirmar(self, *, turno_ref: str, franja_ref: str, afiliado_ref: str, clave: str) -> ConfirmacionAgenda:
        """Pide al sistema de agenda confirmar el cupo. Idempotente por `clave`. Lanza ServicioNoDisponible si falla."""

    def cancelar(self, *, turno_ref: str) -> None:
        """Libera el cupo en la agenda. Lanza ServicioNoDisponible si falla."""

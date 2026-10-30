"""Contexto de la solicitud en curso (IP y correlación).

Lo fija un middleware y lo leen los servicios (por ejemplo `auditoria.registrar`), de modo que las vistas
no tengan que pasar la IP ni auditar a mano.
"""
import uuid
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class Contexto:
    ip: str | None = None
    correlacion: str = ""
    dispositivo: str = ""  # hash del identificador de dispositivo (cookie), nunca el valor crudo


_actual: ContextVar[Contexto] = ContextVar("contexto_solicitud", default=Contexto())


def actual() -> Contexto:
    return _actual.get()


def fijar(ip: str | None, dispositivo: str = "", correlacion: str | None = None):
    return _actual.set(Contexto(ip=ip, correlacion=correlacion or uuid.uuid4().hex, dispositivo=dispositivo))


def restablecer(token) -> None:
    _actual.reset(token)

"""Estado de respuesta automática compartido (Redis): bloqueo temporal de un origen, desafío adaptativo y sala de espera.

Lo ESCRIBE el módulo de detección (acciones automáticas de las reglas D01-D18) y lo LEEN el proxy de la aplicación
(middleware y decorador de endpoints). Solo guarda resúmenes (nunca la IP en claro como parte de la clave).
"""
import hashlib
import time

from django.core.cache import caches


def _cache():
    return caches["limites"]


def _k(*partes: str) -> str:
    return "resp:" + ":".join(partes)


def _huella(valor: str) -> str:
    return hashlib.sha256(valor.encode()).hexdigest()[:32]


# --- Bloqueo temporal del origen (D01, D04) ----------------------------------------------------------------------------------------------


def bloquear_origen(ip: str, segundos: int, motivo: str = "") -> None:
    _cache().set(_k("bloqueo", _huella(ip)), {"hasta": time.time() + segundos, "motivo": motivo}, timeout=segundos)


def origen_bloqueado(ip: str | None) -> int:
    """Segundos que faltan para que termine el bloqueo (0 si no está bloqueado)."""
    if not ip:
        return 0
    dato = _cache().get(_k("bloqueo", _huella(ip)))
    return max(0, int(dato["hasta"] - time.time())) if dato else 0


def desbloquear_origen(ip: str) -> None:
    _cache().delete(_k("bloqueo", _huella(ip)))


# --- Desafío adaptativo (D02): solo ante comportamiento sospechoso ----------------------------------------------------------------------


def activar_desafio(segundos: int) -> None:
    _cache().set(_k("desafio"), True, timeout=segundos)


def desafio_activo() -> bool:
    return bool(_cache().get(_k("desafio")))


def desactivar_desafio() -> None:
    _cache().delete(_k("desafio"))


# --- Sala de espera (D13): cuando la latencia p95 supera el umbral ------------------------------------------------------------------------


def activar_sala_espera(segundos: int) -> None:
    _cache().set(_k("sala_espera"), True, timeout=segundos)


def sala_espera_activa() -> bool:
    return bool(_cache().get(_k("sala_espera")))


def desactivar_sala_espera() -> None:
    _cache().delete(_k("sala_espera"))

"""Límites de solicitudes con ventana fija sobre la caché (Redis). Sin datos personales en las claves.

Las claves usan un resumen (SHA-256) del identificador, nunca la cédula ni la IP en claro.
"""
import hashlib
import time
from dataclasses import dataclass

from django.core.cache import caches


@dataclass(frozen=True)
class Resultado:
    permitido: bool
    restante: int
    reintentar_en: int  # segundos hasta que se reinicie la ventana


def _clave(ambito: str, identificador: str, ventana: int, indice: int) -> str:
    resumen = hashlib.sha256(identificador.encode()).hexdigest()[:32]
    return f"lim:{ambito}:{resumen}:{ventana}:{indice}"


def consumir(ambito: str, identificador: str, maximo: int, ventana_segundos: int) -> Resultado:
    """Cuenta una solicitud; indica si todavía cabe en el límite."""
    cache = caches["limites"]
    ahora = int(time.time())
    indice = ahora // ventana_segundos
    clave = _clave(ambito, identificador, ventana_segundos, indice)
    cache.add(clave, 0, timeout=ventana_segundos + 1)
    cuenta = cache.incr(clave)
    reintentar = (indice + 1) * ventana_segundos - ahora
    return Resultado(permitido=cuenta <= maximo, restante=max(0, maximo - cuenta), reintentar_en=reintentar)

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


def incrementar(alias: str, claves: list[str], ttl: int) -> list[int]:
    """Suma 1 a cada contador (con caducidad solo la primera vez) en UN viaje a Redis. Devuelve los valores nuevos.

    El camino normal de Django (add + incr) son 3 comandos y 3 clientes por contador; en la ruta caliente eso pesaba más que la vista.
    """
    cache = caches[alias]
    cliente_redis = getattr(getattr(cache, "_cache", None), "get_client", None)
    if cliente_redis is None:  # caché que no es Redis (pruebas con memoria local)
        valores = []
        for clave in claves:
            cache.add(clave, 0, timeout=ttl)
            valores.append(cache.incr(clave))
        return valores
    with cliente_redis(write=True).pipeline(transaction=False) as tuberia:
        for clave in claves:
            llave = cache.make_key(clave)
            tuberia.incr(llave)
            tuberia.expire(llave, ttl, nx=True)
        return tuberia.execute()[::2]


def consumir(ambito: str, identificador: str, maximo: int, ventana_segundos: int) -> Resultado:
    """Cuenta una solicitud; indica si todavía cabe en el límite."""
    ahora = int(time.time())
    indice = ahora // ventana_segundos
    clave = _clave(ambito, identificador, ventana_segundos, indice)
    cuenta = incrementar("limites", [clave], ventana_segundos + 1)[0]
    reintentar = (indice + 1) * ventana_segundos - ahora
    return Resultado(permitido=cuenta <= maximo, restante=max(0, maximo - cuenta), reintentar_en=reintentar)

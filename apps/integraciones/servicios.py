"""Fachada pública de las integraciones. Las demás apps solo importan este módulo."""
import hashlib

from django.conf import settings
from django.core.cache import caches

from config.parametros import P

from .adaptadores.http import AfiliacionHttp
from .adaptadores.memoria import AfiliacionMemoria
from .puertos import DatosAfiliacion, PuertoAfiliacion, ServicioNoDisponible
from .sinteticos import generar_afiliados

__all__ = [
    "DatosAfiliacion",
    "ServicioNoDisponible",
    "afiliacion_memoria",
    "consultar_afiliacion",
    "consultar_vigencia",
    "generar_afiliados_sinteticos",
]

_memoria = AfiliacionMemoria()
_http: AfiliacionHttp | None = None


def afiliacion_memoria() -> AfiliacionMemoria:
    """Adaptador en memoria (solo pruebas y siembra sin red)."""
    return _memoria


def _puerto() -> PuertoAfiliacion:
    global _http
    if settings.AFILIACION_ADAPTADOR == "memoria":
        return _memoria
    if _http is None:
        _http = AfiliacionHttp(settings.AFILIACION_URL, settings.SERVICIOS_CLAVE_AFILIACION)
    return _http


def consultar_afiliacion(cedula: str) -> DatosAfiliacion | None:
    """Datos y contacto REGISTRADOS en el servicio de afiliación (fuente de verdad). Sin caché: lleva datos personales.

    Lanza ServicioNoDisponible si el tercero falla; None si la cédula no consta.
    """
    return _puerto().consultar(cedula)


def _clave_cache(cedula: str) -> str:
    return "afiliacion:vigente:" + hashlib.sha256(cedula.encode()).hexdigest()[:32]


def consultar_vigencia(cedula: str) -> bool:
    """Vigencia de la afiliación con caché de 15 min (solo un booleano: sin datos personales en Redis).

    Si el servicio cae y no hay un valor reciente, lanza ServicioNoDisponible (nunca asume cobertura).
    """
    cache = caches["default"]
    clave = _clave_cache(cedula)
    en_cache = cache.get(clave)
    if en_cache is not None:
        return en_cache
    datos = _puerto().consultar(cedula)
    vigente = bool(datos and datos.vigente)
    cache.set(clave, vigente, timeout=P.afiliacion_cache_minutos * 60)
    return vigente


def generar_afiliados_sinteticos() -> list[dict]:
    """Registro sintético completo (solo para la siembra de datos de demostración)."""
    return generar_afiliados()

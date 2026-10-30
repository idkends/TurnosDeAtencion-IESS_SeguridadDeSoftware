"""Autenticación entre servicios con token firmado (HMAC-SHA256 de marca de tiempo y ruta).

Simula mTLS o token de servicio rotado (§4.6, Anexo J). Lo usan el adaptador (cliente) y el simulado (servidor).
"""
import hashlib
import hmac
import time

from config.parametros import P


def firmar(secreto: bytes, ruta: str, ahora: float | None = None) -> str:
    marca = str(int(ahora if ahora is not None else time.time()))
    firma = hmac.new(secreto, f"{marca}:{ruta}".encode(), hashlib.sha256).hexdigest()
    return f"{marca}.{firma}"


def verificar(secreto: bytes, token: str | None, ruta: str, ahora: float | None = None) -> bool:
    if not secreto or not token or "." not in token:
        return False
    marca, _, firma = token.partition(".")
    if not marca.isdigit():
        return False
    ahora = ahora if ahora is not None else time.time()
    if abs(ahora - int(marca)) > P.servicio_token_tolerancia_segundos:
        return False
    esperado = hmac.new(secreto, f"{marca}:{ruta}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, firma)

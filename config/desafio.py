"""Desafío adaptativo sencillo (una operación aritmética firmada), solo cuando la detección lo activa (D02, CA-12).

No usa servicios externos. El enunciado viaja firmado (no se puede fabricar la respuesta) y caduca; cada desafío sirve
una sola vez. Es una barrera contra la automatización barata, no contra un atacante dedicado: las demás capas siguen.
"""
import secrets

from django.core import signing
from django.core.cache import caches

_SAL = "iess.desafio"
_VIGENCIA = 300  # segundos


def crear() -> tuple[str, str]:
    """Devuelve (token firmado, pregunta en español)."""
    a, b = secrets.randbelow(9) + 1, secrets.randbelow(9) + 1
    token = signing.dumps({"r": a + b, "n": secrets.token_hex(8)}, salt=_SAL)
    return token, f"¿Cuánto es {a} + {b}?"


def verificar(token: str | None, respuesta: str | None) -> bool:
    try:
        datos = signing.loads(token or "", salt=_SAL, max_age=_VIGENCIA)
        esperado = int(datos["r"])
        dado = int((respuesta or "").strip())
    except (signing.BadSignature, ValueError, KeyError, TypeError):
        return False
    cache = caches["limites"]
    if not cache.add(f"desafio:usado:{datos['n']}", 1, timeout=_VIGENCIA + 5):
        return False  # ya se usó
    return dado == esperado

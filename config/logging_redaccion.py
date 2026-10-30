"""Redacción de secretos en TODOS los registros de la aplicación (R08, CWE-532, Anexo J).

Se aplica como formateador del manejador raíz: incluso si algún código registrara por error una clave, un token, una
cookie o un dato que parece una cédula, el texto final (mensaje y traza) sale redactado.
"""
import logging
import re

_PARES = re.compile(
    r"(?i)\b(\w*(?:password|passwd|pwd|clave|contrase[ñn]a|token|secret|codigo|otp|authorization|cookie|"
    r"sessionid|csrftoken|csrfmiddlewaretoken|api[_-]?key|diagn[oó]stic)\w*)\b(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|\S+)"
)
_PORTADOR = re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._\-+/=]+")
_COOKIES = re.compile(r"(?i)(__Host-\w+|sessionid|csrftoken)=[^\s;,]+")
_LARGOS = re.compile(r"\b[A-Za-z0-9_\-]{32,}\b")
_DIEZ_DIGITOS = re.compile(r"(?<!\d)\d{10}(?!\d)")  # cédulas y teléfonos


def redactar(texto: str) -> str:
    texto = _PORTADOR.sub(lambda m: f"{m.group(1)} [REDACTADO]", texto)
    texto = _PARES.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTADO]", texto)
    texto = _COOKIES.sub(lambda m: f"{m.group(1)}=[REDACTADO]", texto)
    texto = _LARGOS.sub("[TOKEN]", texto)
    return _DIEZ_DIGITOS.sub("[ID]", texto)


class FormateadorSeguro(logging.Formatter):
    """Da formato como siempre y redacta el resultado completo (mensaje, argumentos y traza de excepción)."""

    def format(self, record: logging.LogRecord) -> str:
        return redactar(super().format(record))

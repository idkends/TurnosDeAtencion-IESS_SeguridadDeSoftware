"""Reloj único de la aplicación. Las pruebas lo reemplazan para simular el paso del tiempo."""
from datetime import datetime, timedelta, timezone as _tz

_desfase = timedelta(0)


def ahora() -> datetime:
    """Instante actual con zona horaria (UTC)."""
    return datetime.now(_tz.utc) + _desfase


def avanzar(delta: timedelta) -> None:
    """Solo para pruebas: adelanta el reloj."""
    global _desfase
    _desfase += delta


def restablecer() -> None:
    global _desfase
    _desfase = timedelta(0)

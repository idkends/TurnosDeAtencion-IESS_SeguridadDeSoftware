"""Puerto de mensajería y sus implementaciones simuladas (consola, archivo, memoria).

El dominio depende de `PuertoMensajeria`; ninguna implementación habla con un proveedor real de SMS o correo.
"""
import json
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class Mensaje:
    canal: str  # CORREO | SMS
    destino: str
    plantilla: str
    asunto: str
    cuerpo: str


class PuertoMensajeria(Protocol):
    def enviar(self, mensaje: Mensaje) -> None: ...


def _enmascarar(destino: str) -> str:
    if "@" in destino:
        local, dominio = destino.split("@", 1)
        return f"{local[:1]}***@{dominio}"
    return f"{destino[:3]}****{destino[-2:]}" if len(destino) > 5 else "***"


class MensajeriaMemoria:
    """Guarda los mensajes en una lista (pruebas)."""

    def __init__(self):
        self.enviados: list[Mensaje] = []

    def enviar(self, mensaje: Mensaje) -> None:
        self.enviados.append(mensaje)

    def limpiar(self) -> None:
        self.enviados.clear()


class MensajeriaConsola:
    """Imprime el mensaje en la salida estándar (simula el buzón del afiliado en desarrollo)."""

    def enviar(self, mensaje: Mensaje) -> None:
        sys.stdout.write(
            f"\n[NOTIFICACION SIMULADA] {mensaje.canal} -> {_enmascarar(mensaje.destino)} "
            f"({mensaje.plantilla})\n  {mensaje.asunto}\n  {mensaje.cuerpo}\n\n"
        )
        sys.stdout.flush()


class MensajeriaArchivo:
    """Escribe cada mensaje como un archivo JSON en un directorio (bandeja de salida de desarrollo)."""

    def __init__(self, directorio: str):
        self.directorio = Path(directorio)

    def enviar(self, mensaje: Mensaje) -> None:
        self.directorio.mkdir(parents=True, exist_ok=True)
        ruta = self.directorio / f"{uuid.uuid4().hex}.json"
        ruta.write_text(
            json.dumps(
                {
                    "canal": mensaje.canal,
                    "destino": _enmascarar(mensaje.destino),
                    "plantilla": mensaje.plantilla,
                    "asunto": mensaje.asunto,
                    "cuerpo": mensaje.cuerpo,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

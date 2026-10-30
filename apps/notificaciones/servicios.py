"""Notificaciones neutrales (§4.5): plantillas fijas, sin diagnóstico, especialidad ni enlaces que abran datos.

Los únicos enlaces permitidos son los de un solo uso de activación/recuperación, armados por quien llama con el
dominio fijo de la configuración. El cuerpo con secretos nunca se registra: solo se guarda plantilla y estado.
"""
import logging

from django.conf import settings

from config import reloj

from .adaptadores import MensajeriaArchivo, MensajeriaConsola, MensajeriaMemoria, Mensaje, PuertoMensajeria
from .modelos import Canal, EstadoNotificacion, Notificacion

logger = logging.getLogger(__name__)

# plantilla -> (asunto, cuerpo). Solo estos textos pueden salir del sistema.
PLANTILLAS: dict[str, tuple[str, str]] = {
    "activacion": (
        "Código de activación del portal de turnos",
        "Su código de activación es {codigo}. Vence en {minutos} minutos y solo sirve una vez. "
        "No lo comparta con nadie; el portal nunca se lo pedirá por mensaje.",
    ),
    "recuperacion": (
        "Restablecer la clave del portal de turnos",
        "Recibimos una solicitud para restablecer su clave. Use este enlace en los próximos {minutos} minutos: "
        "{enlace} . Si no fue usted, ignore este mensaje.",
    ),
    "clave_cambiada": (
        "Su clave fue cambiada",
        "La clave de su cuenta del portal de turnos fue cambiada. Si no fue usted, contacte a su unidad de atención.",
    ),
    "dispositivo_nuevo": (
        "Nuevo inicio de sesión",
        "Se inició sesión en su cuenta desde un dispositivo nuevo el {fecha}. "
        "Si no fue usted, ingrese al portal y revoque esa sesión.",
    ),
    "sesion_revocada": (
        "Se cerró una sesión de su cuenta",
        "Se cerró una de sus sesiones por superar el máximo de dispositivos permitidos.",
    ),
}

_adaptador_memoria = MensajeriaMemoria()


def _adaptador() -> PuertoMensajeria:
    nombre = settings.NOTIFICACIONES_ADAPTADOR
    if nombre == "memoria":
        return _adaptador_memoria
    if nombre == "archivo":
        return MensajeriaArchivo(settings.NOTIFICACIONES_DIRECTORIO)
    return MensajeriaConsola()


def bandeja_memoria() -> MensajeriaMemoria:
    """Acceso a los mensajes del adaptador en memoria (solo pruebas)."""
    return _adaptador_memoria


def enviar(*, plantilla: str, canal: str, destino: str, parametros: dict | None = None, destinatario_id=None) -> bool:
    """Envía una notificación neutral. Devuelve False (sin lanzar) si el adaptador falla o no hay destino."""
    if plantilla not in PLANTILLAS:
        raise ValueError(f"Plantilla no permitida: {plantilla}")
    if not destino:
        return False
    asunto, cuerpo = PLANTILLAS[plantilla]
    mensaje = Mensaje(canal=canal, destino=destino, plantilla=plantilla, asunto=asunto, cuerpo=cuerpo.format(**(parametros or {})))
    try:
        _adaptador().enviar(mensaje)
        estado = EstadoNotificacion.ENVIADA
    except Exception as error:  # un fallo de mensajería no debe tumbar la operación ni revelar si la cuenta existe
        # Solo el tipo de error: el mensaje o el traceback de un adaptador real podrían incluir el cuerpo (con códigos).
        logger.error("Falla al enviar la notificación %s (%s)", plantilla, type(error).__name__)
        estado = EstadoNotificacion.FALLIDA
    Notificacion.objects.create(
        destinatario_id=destinatario_id, canal=Canal(canal), plantilla=plantilla, estado=estado, creada=reloj.ahora()
    )
    return estado == EstadoNotificacion.ENVIADA

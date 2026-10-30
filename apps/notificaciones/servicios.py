"""Notificaciones neutrales (§4.5): plantillas fijas, sin diagnóstico, especialidad ni enlaces que abran datos.

Los únicos enlaces permitidos son los de un solo uso de activación/recuperación, armados por quien llama con el
dominio fijo de la configuración. El cuerpo con secretos nunca se registra: solo se guarda plantilla y estado.
"""
import logging

from django.conf import settings

import hashlib
import secrets
from datetime import timedelta

from django.db import transaction

from apps.auditoria import servicios as auditoria
from config import reloj
from config.parametros import P

from .adaptadores import MensajeriaArchivo, MensajeriaConsola, MensajeriaMemoria, Mensaje, PuertoMensajeria
from .modelos import Canal, EstadoNotificacion, Notificacion, TokenEnlace

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
    # --- Turnos (§4.5): solo fecha y hora; nunca especialidad, profesional ni diagnóstico ---
    "reserva_creada": (
        "Cita pendiente de confirmar",
        "Tiene una cita pendiente de confirmar. Ingrese al portal.",
    ),
    "cita_confirmada": (
        "Cita confirmada",
        "Tiene una cita el {fecha}. Ingrese al portal.",
    ),
    "cita_cancelada": (
        "Cita cancelada",
        "Su cita fue cancelada. Ingrese al portal.",
    ),
    "cita_expirada": (
        "Cita sin confirmar",
        "Su cita no fue confirmada a tiempo y el cupo se liberó. Ingrese al portal si desea reservar de nuevo.",
    ),
    "cita_reprogramada_institucion": (
        "Cita reprogramada",
        "Su cita fue reprogramada. Ingrese al portal.",
    ),
    "inasistencia_registrada": (
        "Inasistencia registrada",
        "Se registró una inasistencia a una de sus citas. {consecuencia} Puede justificarla dentro de {horas} horas y "
        "apelar ante una persona de su unidad. La atención presencial y telefónica siempre está disponible.",
    ),
    "restriccion_levantada": (
        "Restricción levantada",
        "Se levantó una restricción de su cuenta. Ya puede reservar con normalidad.",
    ),
    "apelacion_recibida": (
        "Apelación recibida",
        "Recibimos su apelación. Le responderemos antes del {fecha}.",
    ),
    "apelacion_resuelta": (
        "Apelación resuelta",
        "Su apelación fue resuelta. Ingrese al portal para ver el resultado.",
    ),
    "accion_representante": (
        "Se gestionó una cita con su delegación",
        "Se gestionó una cita con su delegación.",
    ),
    "delegacion_creada": (
        "Nueva delegación en su cuenta",
        "Se registró una delegación de agenda en su cuenta. Puede revocarla en cualquier momento desde el portal.",
    ),
    "delegacion_revocada": (
        "Delegación revocada",
        "Se revocó una delegación de agenda de su cuenta.",
    ),
    "alerta_seguridad": (
        "Actividad inusual en su cuenta",
        "Detectamos intentos de acceso inusuales en su cuenta y la protegimos. Para volver a entrar, restablezca su clave desde el portal.",
    ),
    "alerta_seguridad_interna": (
        "Alerta de seguridad pendiente",
        "Hay una alerta de seguridad de severidad alta o crítica pendiente en el panel de alertas.",
    ),
    "contacto_cambiado": (
        "Se cambió el contacto de su cuenta",
        "Se cambió el contacto registrado en su cuenta del portal de turnos. Si no fue usted, comuníquese con su unidad de atención.",
    ),
    "recordatorio": (
        "Recordatorio de cita",
        "Tiene una cita el {fecha}. Ingrese al portal para ver el detalle o cancelar: {enlace}",
    ),
    "emergencia_acceso": (
        "Acceso excepcional registrado",
        "Se registró un acceso excepcional (de emergencia) a datos de un afiliado. Corresponde su revisión posterior.",
    ),
    "exportacion_pendiente": (
        "Exportación pendiente de aprobación",
        "Hay una solicitud de exportación puntual que requiere su aprobación en el portal.",
    ),
    "encargado_eliminacion": (
        "Orden de eliminación de datos",
        "Se solicita eliminar los datos asociados a una cuenta dentro del plazo contractual.",
    ),
    "derechos_recibida": (
        "Solicitud recibida",
        "Recibimos su solicitud sobre sus datos. Le responderemos antes del {fecha}.",
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


# --- Enlaces de un solo uso de los avisos (E20, R03) -----------------------------------------------------------------------------


def crear_enlace(*, destinatario_id, turno_id) -> str:
    """Token de 128 bits (se guarda solo su hash) que caduca en 24 h. Quien lo use solo llega a iniciar sesión."""
    token = secrets.token_urlsafe(16)
    ahora = reloj.ahora()
    TokenEnlace.objects.create(
        destinatario_id=destinatario_id,
        turno_id=turno_id,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        creado=ahora,
        expira=ahora + timedelta(hours=P.enlace_token_horas),
    )
    return token


def consumir_enlace(token: str):
    """Invalida el token y devuelve el id del turno al que apunta, o None si no existe, venció o ya se usó.

    Una sola transacción con bloqueo de fila: dos usos simultáneos no pueden ganar ambos. Todo intento (bueno o malo)
    queda auditado con la referencia del token, de modo que la detección (D17) vea el mismo token desde orígenes distintos.
    """
    resumen = hashlib.sha256((token or "").encode()).hexdigest()
    turno_id = None
    with transaction.atomic():
        fila = TokenEnlace.objects.select_for_update().filter(token_hash=resumen, usado=False).first()
        if fila is not None and reloj.ahora() < fila.expira:
            fila.usado = True
            fila.save(update_fields=["usado"])
            turno_id = fila.turno_id
            destinatario = fila.destinatario_id
    if turno_id is None:
        auditoria.registrar(accion=auditoria.Acciones.ENLACE_RECHAZADO, resultado=auditoria.DENEGADO, recurso_tipo="enlace", detalle={"token_ref": resumen[:12]})
        return None
    auditoria.registrar(accion=auditoria.Acciones.ENLACE_USADO, actor_id=destinatario, recurso_tipo="turno", recurso_id=turno_id, detalle={"token_ref": resumen[:12]})
    return turno_id


def invalidar_enlace_por_ref(token_ref: str) -> int:
    """Respuesta a D17: invalida un enlace presentado desde orígenes distintos."""
    return TokenEnlace.objects.filter(token_hash__startswith=token_ref, usado=False).update(usado=True)


def purgar_antiguas(meses: int) -> int:
    """Registro de notificaciones: 6 meses (prueba de aviso); después se elimina. También los enlaces vencidos."""
    corte = reloj.ahora() - timedelta(days=P.dias_por_mes * meses)
    n = Notificacion.objects.filter(creada__lt=corte).delete()[0]
    n += TokenEnlace.objects.filter(expira__lt=reloj.ahora() - timedelta(days=P.enlaces_vencidos_conservacion_dias)).delete()[0]
    return n

"""Servicio de auditoría: ÚNICO punto de escritura de la cadena de hashes (R22, RT-12).

Cada evento incluye el hash del anterior y un HMAC-SHA256 con una clave propia (separada de SECRET_KEY) y
versionada. Ni contraseñas, ni tokens, ni datos clínicos: el detalle pasa por un filtro de redacción.
"""
import hashlib
import hmac
import ipaddress
import json
import re
import uuid
from dataclasses import dataclass

from django.conf import settings
from django.db import connection, transaction

from config import contexto, reloj
from config.parametros import P

from .modelos import EventoAuditoria

HASH_GENESIS = "0" * 64
_BLOQUEO_CADENA = 7_246_001  # clave del bloqueo consultivo que serializa la escritura de la cadena

OK = EventoAuditoria.Resultado.OK.value
DENEGADO = EventoAuditoria.Resultado.DENEGADO.value
ERROR = EventoAuditoria.Resultado.ERROR.value


class Acciones:
    """Catálogo de acciones auditables (cadenas fijas: la detección de la etapa 3 se apoya en ellas)."""

    ACTIVACION_SOLICITADA = "ACTIVACION_SOLICITADA"
    ACTIVACION_COMPLETADA = "ACTIVACION_COMPLETADA"
    ACTIVACION_FALLIDA = "ACTIVACION_FALLIDA"
    LOGIN_OK = "LOGIN_OK"
    LOGIN_FALLIDO = "LOGIN_FALLIDO"
    LOGIN_BLOQUEADO = "LOGIN_BLOQUEADO"
    LIMITE_EXCEDIDO = "LIMITE_EXCEDIDO"
    MFA_OK = "MFA_OK"
    MFA_FALLIDO = "MFA_FALLIDO"
    MFA_CONFIGURADO = "MFA_CONFIGURADO"
    MFA_CODIGO_RECUPERACION_USADO = "MFA_CODIGO_RECUPERACION_USADO"
    SESION_INICIADA = "SESION_INICIADA"
    SESION_REVOCADA = "SESION_REVOCADA"
    SESION_CERRADA = "SESION_CERRADA"
    DISPOSITIVO_NUEVO = "DISPOSITIVO_NUEVO"
    CLAVE_CAMBIADA = "CLAVE_CAMBIADA"
    RECUPERACION_SOLICITADA = "RECUPERACION_SOLICITADA"
    RECUPERACION_COMPLETADA = "RECUPERACION_COMPLETADA"
    RECUPERACION_FALLIDA = "RECUPERACION_FALLIDA"
    ACCESO_DENEGADO = "ACCESO_DENEGADO"
    SIEMBRA = "SIEMBRA"
    # --- Turnos y delegaciones (etapa 2) ---
    RESERVA_CREADA = "RESERVA_CREADA"
    RESERVA_RECHAZADA = "RESERVA_RECHAZADA"
    RESERVA_REPETIDA = "RESERVA_REPETIDA"
    TURNO_RESERVADO = "TURNO_RESERVADO"
    TURNO_CONFIRMADO = "TURNO_CONFIRMADO"
    TURNO_CANCELADO = "TURNO_CANCELADO"
    CANCELACION_DESHECHA = "CANCELACION_DESHECHA"
    TURNO_REPROGRAMADO = "TURNO_REPROGRAMADO"
    TURNO_EXPIRADO = "TURNO_EXPIRADO"
    TURNO_CANCELADO_INSTITUCION = "TURNO_CANCELADO_INSTITUCION"
    AGENDA_NO_DISPONIBLE = "AGENDA_NO_DISPONIBLE"
    AFILIACION_NO_DISPONIBLE = "AFILIACION_NO_DISPONIBLE"
    ASISTENCIA_REGISTRADA = "ASISTENCIA_REGISTRADA"
    INASISTENCIA_REGISTRADA = "INASISTENCIA_REGISTRADA"
    INASISTENCIA_JUSTIFICADA = "INASISTENCIA_JUSTIFICADA"
    INASISTENCIAS_REVISION_HUMANA = "INASISTENCIAS_REVISION_HUMANA"
    RESTRICCION_APLICADA = "RESTRICCION_APLICADA"
    RESTRICCION_LEVANTADA = "RESTRICCION_LEVANTADA"
    APELACION_CREADA = "APELACION_CREADA"
    APELACION_RESUELTA = "APELACION_RESUELTA"
    DELEGACION_CREADA = "DELEGACION_CREADA"
    DELEGACION_REVOCADA = "DELEGACION_REVOCADA"
    DELEGACION_RECHAZADA = "DELEGACION_RECHAZADA"
    CONSULTA_CONTACTO = "CONSULTA_CONTACTO"
    SOLICITUD_DERECHOS = "SOLICITUD_DERECHOS"
    ENLACE_USADO = "ENLACE_USADO"
    ACCESO_AUDITORIA = "ACCESO_AUDITORIA"


_CLAVES_SENSIBLES = re.compile(
    r"(clave|contrase|password|passwd|token|secret|totp|otp|codigo_activacion|codigo_recuperacion|hash|authorization|cookie)",
    re.IGNORECASE,
)
_REDACTADO = "[REDACTADO]"


def _redactar(valor, profundidad: int = 0):
    """Elimina secretos y recorta textos largos. Los diccionarios se filtran por nombre de clave."""
    if profundidad > 4:
        return _REDACTADO
    if isinstance(valor, dict):
        return {
            str(k)[:60]: (_REDACTADO if _CLAVES_SENSIBLES.search(str(k)) else _redactar(v, profundidad + 1))
            for k, v in valor.items()
        }
    if isinstance(valor, (list, tuple)):
        return [_redactar(v, profundidad + 1) for v in list(valor)[:50]]
    if isinstance(valor, (int, float, bool)) or valor is None:
        return valor
    return str(valor)[: P.auditoria_detalle_maximo_caracteres]


def _normalizar_ip(ip: str | None) -> str | None:
    """Forma canónica (la que devuelve PostgreSQL), para que el hash no cambie al releer el evento."""
    if not ip:
        return None
    try:
        return ipaddress.ip_address(ip).compressed
    except ValueError:
        return None


def _carga_canonica(evento: EventoAuditoria) -> bytes:
    carga = {
        "uuid": str(evento.uuid),
        "ocurrido_en": evento.ocurrido_en.isoformat(),
        "actor_id": str(evento.actor_id) if evento.actor_id else None,
        "actor_rol": evento.actor_rol,
        "accion": evento.accion,
        "recurso_tipo": evento.recurso_tipo,
        "recurso_id": evento.recurso_id,
        "resultado": evento.resultado,
        "ip": evento.ip,
        "correlacion": evento.correlacion,
        "detalle": evento.detalle,
        "hash_anterior": evento.hash_anterior,
        "version_clave": evento.version_clave,
    }
    return json.dumps(carga, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _firmar(evento: EventoAuditoria, version: int) -> str:
    clave = settings.AUDITORIA_CLAVES[version]
    return hmac.new(clave, _carga_canonica(evento), hashlib.sha256).hexdigest()


def registrar(
    *,
    accion: str,
    actor=None,
    actor_id=None,
    actor_rol: str = "",
    recurso_tipo: str = "",
    recurso_id="",
    resultado: str = OK,
    detalle: dict | None = None,
    ip: str | None = None,
) -> EventoAuditoria:
    """Agrega un evento a la cadena. IP y correlación salen del contexto de la solicitud si no se indican.

    `actor` es cualquier objeto con `.id` y `.rol` (la auditoría no importa modelos de otras apps).
    """
    if actor is not None:
        actor_id = actor_id or getattr(actor, "id", None)
        actor_rol = actor_rol or getattr(actor, "rol", "")
    ctx = contexto.actual()
    ip = _normalizar_ip(ip or ctx.ip)
    version = settings.AUDITORIA_VERSION_ACTUAL
    with transaction.atomic():
        with connection.cursor() as cursor:
            # Serializa a los escritores: dos eventos simultáneos no pueden encadenarse al mismo anterior.
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [_BLOQUEO_CADENA])
        ultimo = EventoAuditoria.objects.order_by("-secuencia").only("hash").first()
        evento = EventoAuditoria(
            uuid=uuid.uuid4(),
            ocurrido_en=reloj.ahora(),
            actor_id=actor_id,
            actor_rol=str(actor_rol or ""),
            accion=accion,
            recurso_tipo=recurso_tipo,
            recurso_id=str(recurso_id or ""),
            resultado=resultado,
            ip=ip,
            correlacion=ctx.correlacion,
            detalle=_redactar(detalle or {}),
            hash_anterior=ultimo.hash if ultimo else HASH_GENESIS,
            version_clave=version,
        )
        evento.hash = _firmar(evento, version)
        evento.save()
    return evento


@dataclass(frozen=True)
class ResultadoVerificacion:
    correcta: bool
    eventos_revisados: int
    primera_falla: int | None = None  # secuencia del primer evento que no coincide
    motivo: str = ""


def verificar_cadena() -> ResultadoVerificacion:
    """Recorre la cadena completa y detecta cualquier alteración o eslabón perdido (T11b)."""
    anterior = HASH_GENESIS
    revisados = 0
    for evento in EventoAuditoria.objects.order_by("secuencia").iterator(chunk_size=500):
        revisados += 1
        if evento.hash_anterior != anterior:
            return ResultadoVerificacion(False, revisados, evento.secuencia, "eslabón anterior no coincide")
        if evento.version_clave not in settings.AUDITORIA_CLAVES:
            return ResultadoVerificacion(False, revisados, evento.secuencia, "versión de clave desconocida")
        esperado = _firmar(evento, evento.version_clave)
        if not hmac.compare_digest(esperado, evento.hash):
            return ResultadoVerificacion(False, revisados, evento.secuencia, "contenido alterado")
        anterior = evento.hash
    return ResultadoVerificacion(True, revisados)


# --- Consulta (E19): solo lectura, con filtros en lista blanca y paginación --------------------------------------------------------


def acciones_validas() -> set[str]:
    return {v for k, v in vars(Acciones).items() if k.isupper() and isinstance(v, str)}


@dataclass(frozen=True)
class PaginaEventos:
    eventos: list
    pagina: int
    tamano: int
    total: int

    @property
    def hay_siguiente(self) -> bool:
        return self.pagina * self.tamano < self.total


def listar_eventos(*, accion=None, resultado=None, actor_id=None, desde=None, hasta=None, pagina: int = 1, tamano: int | None = None) -> PaginaEventos:
    """Eventos más recientes primero. Los filtros ya vienen validados (lista blanca); nunca se arma SQL con ellos."""
    tamano = max(1, min(tamano or P.paginacion_por_defecto, P.paginacion_maxima))
    pagina = max(1, pagina)
    consulta = EventoAuditoria.objects.all()
    if accion:
        consulta = consulta.filter(accion=accion)
    if resultado:
        consulta = consulta.filter(resultado=resultado)
    if actor_id:
        consulta = consulta.filter(actor_id=actor_id)
    if desde:
        consulta = consulta.filter(ocurrido_en__gte=desde)
    if hasta:
        consulta = consulta.filter(ocurrido_en__lt=hasta)
    total = consulta.count()
    inicio = (pagina - 1) * tamano
    filas = list(
        consulta.order_by("-secuencia").values(
            "secuencia", "ocurrido_en", "actor_rol", "accion", "recurso_tipo", "recurso_id", "resultado", "ip", "correlacion", "detalle"
        )[inicio : inicio + tamano]
    )
    return PaginaEventos(filas, pagina, tamano, total)

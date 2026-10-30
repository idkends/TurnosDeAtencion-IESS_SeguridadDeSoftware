"""Servicios de delegación (§4.4): un familiar ayuda con la AGENDA del titular. Nunca hay datos clínicos.

Reglas: el titular (con segundo factor reciente) o un tutor verificado en ventanilla crea la delegación; alcance solo
AGENDA; vigencia de 12 meses; hasta 5 representados por representante (RT-08); revocación inmediata (surte efecto en la
siguiente solicitud, sin esperar a que caduque la sesión); aviso neutral al titular por cada acción del representante;
auditoría con ambos identificadores.
"""
import calendar
from dataclasses import dataclass
from datetime import datetime

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction

from apps.auditoria import servicios as auditoria
from apps.auditoria.servicios import Acciones
from apps.cuentas import politicas
from apps.cuentas import servicios as cuentas
from apps.cuentas.servicios import AccesoDenegado, NoEncontrado
from apps.notificaciones import servicios as notificaciones
from config import reloj
from config.parametros import P

from .modelos import Delegacion, EstadoDelegacion, TipoDelegacion

__all__ = [
    "AccesoDenegado",
    "DelegacionRechazada",
    "DelegacionVista",
    "NoEncontrado",
    "crear_delegacion",
    "crear_delegacion_tutor",
    "delegacion_vigente",
    "listar_delegaciones",
    "notificar_accion_representante",
    "previsualizar_representante",
    "representados_de",
    "revocar_delegacion",
    "revocar_todas_de",
    "purgar_vencidas",
]


class DelegacionRechazada(Exception):
    """Mensaje genérico: no revela si la cédula consultada tiene cuenta (R18)."""

    def __init__(self, codigo: str):
        super().__init__(codigo)
        self.codigo = codigo


@dataclass(frozen=True)
class DelegacionVista:
    id: object
    titular_id: object
    titular_nombre: str
    representante_id: object
    representante_nombre: str
    tipo: str
    estado: str
    inicio: datetime
    vence: datetime
    vigente: bool
    puede_revocar: bool = False


def delegacion_vigente(titular_id, representante_id) -> bool:
    """¿Existe una delegación ACTIVA y no vencida del titular al representante? Se evalúa en cada solicitud."""
    ahora = reloj.ahora()
    return Delegacion.objects.filter(
        titular_id=titular_id, representante_id=representante_id, estado=EstadoDelegacion.ACTIVA, inicio__lte=ahora, vence__gt=ahora
    ).exists()


def _sumar_meses(instante: datetime, meses: int) -> datetime:
    indice = instante.month - 1 + meses
    anio, mes = instante.year + indice // 12, indice % 12 + 1
    return instante.replace(year=anio, month=mes, day=min(instante.day, calendar.monthrange(anio, mes)[1]))


def _vista(d: Delegacion, nombres: dict, usuario=None) -> DelegacionVista:
    ahora = reloj.ahora()
    vigente = d.estado == EstadoDelegacion.ACTIVA and d.inicio <= ahora < d.vence
    return DelegacionVista(
        id=d.id,
        titular_id=d.titular_id,
        titular_nombre=nombres.get(d.titular_id, ""),
        representante_id=d.representante_id,
        representante_nombre=nombres.get(d.representante_id, ""),
        tipo=d.tipo,
        estado=d.estado,
        inicio=d.inicio,
        vence=d.vence,
        vigente=vigente,
        puede_revocar=bool(vigente and usuario is not None and politicas.puede_revocar_delegacion(usuario, d)),
    )


def _nombres(delegaciones) -> dict:
    ids = {d.titular_id for d in delegaciones} | {d.representante_id for d in delegaciones}
    return {i: p.nombre_completo for i, p in cuentas.personas_por_ids(ids).items()}


def previsualizar_representante(request, cedula: str):
    """Paso de confirmación: muestra el NOMBRE de la persona antes de crear la delegación (error de tipeo de cédula)."""
    if not politicas.puede_crear_delegacion(request.user):
        raise AccesoDenegado()
    # También la previsualización pide segundo factor reciente: sin esto serviría para averiguar nombres por cédula (R18).
    cuentas.exigir_operacion_sensible(request)
    persona = cuentas.persona_por_cedula(cedula)
    if persona is None or persona.estado != "ACTIVA" or persona.rol != "REPRESENTANTE" or persona.id == request.user.id:
        raise DelegacionRechazada("REPRESENTANTE_NO_VALIDO")
    return persona


def _crear(*, titular, representante, tipo, verificador_id=None) -> DelegacionVista:
    ahora = reloj.ahora()
    try:
        with transaction.atomic():
            with connection.cursor() as cursor:  # serializa altas simultáneas del mismo representante (tope de 5)
                cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [f"delegaciones:{representante.id}"])
            activas = Delegacion.objects.filter(representante_id=representante.id, estado=EstadoDelegacion.ACTIVA, vence__gt=ahora)
            existente = activas.filter(titular_id=titular.id).first()
            if existente is not None:
                return _vista(existente, _nombres([existente]), titular)  # idempotente: ya existe
            if activas.count() >= P.turnos_representados_por_representante:
                raise DelegacionRechazada("LIMITE_REPRESENTADOS")
            delegacion = Delegacion.objects.create(
                titular_id=titular.id, representante_id=representante.id, tipo=tipo, inicio=ahora,
                vence=_sumar_meses(ahora, P.delegacion_vigencia_meses), verificada_por_id=verificador_id,
            )
            auditoria.registrar(
                accion=Acciones.DELEGACION_CREADA, actor_id=titular.id, actor_rol=titular.rol, recurso_tipo="delegacion", recurso_id=delegacion.id,
                detalle={"titular_id": str(titular.id), "representante_id": str(representante.id), "tipo": tipo, "verificada_por": str(verificador_id or "")},
            )
    except IntegrityError:
        raise DelegacionRechazada("YA_EXISTE") from None
    _avisar(titular.cedula, "delegacion_creada", titular.id)
    return _vista(delegacion, _nombres([delegacion]), None)


def crear_delegacion(request, cedula_representante: str) -> DelegacionVista:
    """E13: el titular, con segundo factor reciente, delega la agenda en un representante."""
    titular = request.user
    if not politicas.puede_crear_delegacion(titular):
        raise AccesoDenegado()
    cuentas.exigir_operacion_sensible(request)  # segundo factor reciente (puede lanzar ReautenticacionRequerida)
    try:
        representante = previsualizar_representante(request, cedula_representante)
        return _crear(titular=titular, representante=representante, tipo=TipoDelegacion.TITULAR)
    except DelegacionRechazada as error:
        auditoria.registrar(
            accion=Acciones.DELEGACION_RECHAZADA, actor=titular, resultado=auditoria.DENEGADO, recurso_tipo="delegacion",
            detalle={"motivo": error.codigo},
        )
        raise


def crear_delegacion_tutor(verificador, cedula_titular: str, cedula_representante: str) -> DelegacionVista:
    """Tutor legal verificado documentalmente en ventanilla (menores y personas bajo tutela): lo registra recepción."""
    if not (politicas.puede_resolver_apelacion(verificador)):  # recepción con unidad asignada
        raise AccesoDenegado()
    titular = cuentas.persona_por_cedula(cedula_titular)
    representante = cuentas.persona_por_cedula(cedula_representante)
    if titular is None or representante is None or representante.rol != "REPRESENTANTE" or representante.estado != "ACTIVA" or titular.id == representante.id:
        raise DelegacionRechazada("DATOS_NO_VALIDOS")
    return _crear(titular=titular, representante=representante, tipo=TipoDelegacion.TUTOR_LEGAL, verificador_id=verificador.id)


def revocar_delegacion(usuario, delegacion_id) -> DelegacionVista:
    """E14: el titular o el propio representante revoca. Surte efecto de inmediato. Idempotente."""
    try:
        d = Delegacion.objects.filter(pk=delegacion_id).first()
    except (ValueError, TypeError, ValidationError):
        d = None
    if d is None or not politicas.puede_revocar_delegacion(usuario, d):
        if d is not None:
            auditoria.registrar(accion=Acciones.ACCESO_DENEGADO, actor=usuario, resultado=auditoria.DENEGADO, recurso_tipo="delegacion", recurso_id=d.id)
        raise NoEncontrado()  # misma respuesta para "no existe" y "no es suya"
    with transaction.atomic():
        d = Delegacion.objects.select_for_update().get(pk=d.pk)
        if d.estado == EstadoDelegacion.ACTIVA:
            d.estado = EstadoDelegacion.REVOCADA
            d.revocada_en = reloj.ahora()
            d.save(update_fields=["estado", "revocada_en"])
            auditoria.registrar(
                accion=Acciones.DELEGACION_REVOCADA, actor=usuario, recurso_tipo="delegacion", recurso_id=d.id,
                detalle={"titular_id": str(d.titular_id), "representante_id": str(d.representante_id)},
            )
            revocada_ahora = True
        else:
            revocada_ahora = False
    if revocada_ahora:
        titular = cuentas.persona_por_id(d.titular_id)
        if titular is not None:
            _avisar(titular.cedula, "delegacion_revocada", titular.id)
    return _vista(d, _nombres([d]), usuario)


def listar_delegaciones(usuario) -> tuple[list[DelegacionVista], list[DelegacionVista]]:
    """(otorgadas por el usuario, recibidas por el usuario): solo las vigentes."""
    if not politicas.puede_reservar_en_linea(usuario):
        raise AccesoDenegado()
    ahora = reloj.ahora()
    base = Delegacion.objects.filter(estado=EstadoDelegacion.ACTIVA, vence__gt=ahora)
    otorgadas = list(base.filter(titular_id=usuario.id).order_by("-inicio")[:50])
    recibidas = list(base.filter(representante_id=usuario.id).order_by("-inicio")[:50])
    nombres = _nombres(otorgadas + recibidas)
    return [_vista(d, nombres, usuario) for d in otorgadas], [_vista(d, nombres, usuario) for d in recibidas]


def representados_de(usuario):
    """Personas cuya agenda puede operar el usuario con una delegación vigente (para elegir a quién atender)."""
    if not politicas.puede_reservar_en_linea(usuario):
        return []
    ahora = reloj.ahora()
    ids = Delegacion.objects.filter(representante_id=usuario.id, estado=EstadoDelegacion.ACTIVA, inicio__lte=ahora, vence__gt=ahora).values_list("titular_id", flat=True)
    personas = cuentas.personas_por_ids(list(ids))
    return sorted(personas.values(), key=lambda p: p.nombre_completo)


def _avisar(cedula: str, plantilla: str, destinatario_id) -> None:
    contacto = cuentas.contacto_por_cedula(cedula)
    if contacto is not None:
        notificaciones.enviar(plantilla=plantilla, canal="CORREO", destino=contacto.correo, destinatario_id=destinatario_id)


def notificar_accion_representante(titular_id, representante_id) -> None:
    """Aviso neutral al titular por CADA acción de su representante («Se gestionó una cita con su delegación»)."""
    titular = cuentas.persona_por_id(titular_id)
    if titular is not None and titular_id != representante_id:
        _avisar(titular.cedula, "accion_representante", titular.id)


def revocar_todas_de(persona_id) -> int:
    """Eliminación de una cuenta: se revocan todas las delegaciones en las que participa."""
    ahora = reloj.ahora()
    n = Delegacion.objects.filter(estado=EstadoDelegacion.ACTIVA).filter(titular_id=persona_id).update(estado=EstadoDelegacion.REVOCADA, revocada_en=ahora)
    n += Delegacion.objects.filter(estado=EstadoDelegacion.ACTIVA).filter(representante_id=persona_id).update(estado=EstadoDelegacion.REVOCADA, revocada_en=ahora)
    return n


def purgar_vencidas(meses: int) -> int:
    """Relaciones de representación: vigencia más 12 meses tras posibles controversias; luego se eliminan (sección 2.2)."""
    from datetime import timedelta

    corte = reloj.ahora() - timedelta(days=30 * meses)
    from django.db.models import Q

    return Delegacion.objects.filter(Q(vence__lt=corte) | Q(revocada_en__lt=corte)).delete()[0]

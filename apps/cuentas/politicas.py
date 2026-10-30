"""Políticas de autorización: ÚNICO lugar donde se decide quién puede hacer qué (§4.3). Funciones PURAS.

- Sin ORM ni E/S: reciben hechos ya resueltos (por ejemplo, si existe una delegación vigente) que entregan los
  servicios. Así este módulo no depende de las demás apps y todas pueden usarlo.
- Deniega por defecto: cualquier combinación no prevista expresamente devuelve False.
- Prohibido comprobar permisos con `if` sueltos en vistas o servicios: se llama a una función de aquí.
"""
from enum import Enum

from config.parametros import P

from .roles import ROLES_FUNCIONARIO, EstadoCuenta, Rol  # noqa: F401  (Rol se reexporta para las demás apps)


class Permiso(str, Enum):
    AGENDA_PROPIA = "agenda_propia"
    CITAS_OTRA_PERSONA = "citas_otra_persona"
    AGENDA_UNIDAD = "agenda_unidad"
    DATOS_CONTACTO = "datos_contacto"
    DATOS_CLINICOS = "datos_clinicos"
    CONFIGURAR_OFERTA = "configurar_oferta"
    REGISTROS_AUDITORIA = "registros_auditoria"
    DESCARGA_MASIVA = "descarga_masiva"


# Matriz de la sección 4.3. S sí; C condicionado a una relación asistencial, una delegación o una justificación
# registrada; U solo su unidad; M enmascarado; L solo lectura; T solo registros técnicos; I solo infraestructura.
MATRIZ: dict[str, dict[Permiso, str]] = {
    Rol.AFILIADO: {Permiso.AGENDA_PROPIA: "S", Permiso.DATOS_CONTACTO: "S"},
    Rol.REPRESENTANTE: {Permiso.AGENDA_PROPIA: "S", Permiso.CITAS_OTRA_PERSONA: "C", Permiso.DATOS_CONTACTO: "C"},
    Rol.RECEPCION: {Permiso.CITAS_OTRA_PERSONA: "C", Permiso.AGENDA_UNIDAD: "U", Permiso.DATOS_CONTACTO: "M"},
    Rol.PROFESIONAL: {Permiso.AGENDA_UNIDAD: "U", Permiso.DATOS_CONTACTO: "M", Permiso.DATOS_CLINICOS: "C"},
    Rol.SOPORTE: {Permiso.DATOS_CONTACTO: "M", Permiso.REGISTROS_AUDITORIA: "T"},
    Rol.ADMIN_OFERTA: {Permiso.AGENDA_UNIDAD: "U", Permiso.CONFIGURAR_OFERTA: "S"},
    Rol.ADMIN_TI: {Permiso.CONFIGURAR_OFERTA: "I"},
    Rol.AUDITOR_DPD: {
        Permiso.AGENDA_UNIDAD: "L",
        Permiso.DATOS_CONTACTO: "C",
        Permiso.REGISTROS_AUDITORIA: "L",
        Permiso.DESCARGA_MASIVA: "C",
    },
}


def nivel(usuario, permiso: Permiso) -> str:
    """Nivel concedido por la matriz ("" = ninguno). Una cuenta no activa no tiene ningún permiso."""
    if not _activo(usuario):
        return ""
    return MATRIZ.get(usuario.rol, {}).get(permiso, "")


def _activo(usuario) -> bool:
    return bool(
        usuario is not None
        and getattr(usuario, "is_authenticated", False)
        and getattr(usuario, "estado", None) == EstadoCuenta.ACTIVA
    )


# --- Turnos (se amplía en la etapa 2) -----------------------------------------------------------------------------
def puede_ver_turno(usuario, turno, *, delegacion_vigente: bool = False, relacion_asistencial: bool = False) -> bool:
    """¿Puede `usuario` ver este turno? `turno` expone `afiliado_id` y `establecimiento_id`.

    El mismo resultado debe usarse para "no existe" y "no es suyo" (404 en ambos casos, CA-07).
    """
    if not _activo(usuario):
        return False
    if usuario.id == turno.afiliado_id:
        return True  # agenda propia
    if delegacion_vigente and nivel(usuario, Permiso.CITAS_OTRA_PERSONA) == "C":
        return True  # representante con delegación vigente (la comprueba el servicio de delegaciones)
    misma_unidad = usuario.establecimiento_id is not None and usuario.establecimiento_id == turno.establecimiento_id
    if usuario.rol in (Rol.RECEPCION, Rol.ADMIN_OFERTA):
        return misma_unidad
    if usuario.rol == Rol.PROFESIONAL:
        return misma_unidad and relacion_asistencial
    return False  # soporte, TI y auditoría no ven fichas individuales de turnos (R06)


# --- Cuenta, sesiones y segundo factor -----------------------------------------------------------------------------
def puede_iniciar_sesion(usuario) -> bool:
    return usuario is not None and getattr(usuario, "estado", None) == EstadoCuenta.ACTIVA


def requiere_mfa(usuario) -> bool:
    """Funcionarios: siempre. Resto de personas: si ya activaron el segundo factor."""
    return usuario.rol in ROLES_FUNCIONARIO or bool(usuario.totp_confirmado)


def debe_configurar_mfa(usuario) -> bool:
    """Los funcionarios no pueden operar sin segundo factor configurado."""
    return usuario.rol in ROLES_FUNCIONARIO and not usuario.totp_confirmado


def puede_gestionar_sesion(usuario, usuario_id_de_la_sesion) -> bool:
    """Solo se pueden listar y revocar las sesiones propias."""
    return _activo(usuario) and usuario.id == usuario_id_de_la_sesion


def sesion_apta_para_operacion_sensible(*, ahora_ts: float, ultima_actividad_ts: float | None, mfa_en_ts: float | None) -> str:
    """Decide si una sesión puede ejecutar una operación sensible. Devuelve "" si es apta o el motivo del rechazo.

    Reglas (Anexo E): hasta 1 h de inactividad y segundo factor verificado recientemente.
    """
    if ultima_actividad_ts is None or ahora_ts - ultima_actividad_ts > P.sesion_inactividad_sensible_minutos * 60:
        return "INACTIVIDAD"
    if mfa_en_ts is None or ahora_ts - mfa_en_ts > P.mfa_reciente_minutos * 60:
        return "MFA_NO_RECIENTE"
    return ""


# --- Turnos, delegaciones e interno (etapa 2) ----------------------------------------------------------------------------------------
# Siguen siendo funciones PURAS: los hechos que salen de la base (¿delegación vigente?, ¿justificación escrita?) los
# resuelven los servicios y se pasan aquí.

ROLES_CIUDADANOS = frozenset({Rol.AFILIADO, Rol.REPRESENTANTE})


def _es(usuario, *roles) -> bool:
    return _activo(usuario) and usuario.rol in roles


def puede_reservar_en_linea(usuario) -> bool:
    """La reserva en línea es de afiliados y representantes; los funcionarios no reservan desde el portal."""
    return _es(usuario, Rol.AFILIADO, Rol.REPRESENTANTE)


def puede_gestionar_turno(usuario, turno, *, delegacion_vigente: bool = False) -> bool:
    """Confirmar, cancelar, reprogramar: SOLO el titular o su representante con delegación vigente (nunca el personal)."""
    if not puede_reservar_en_linea(usuario):
        return False
    if usuario.id == turno.afiliado_id:
        return True
    return delegacion_vigente and nivel(usuario, Permiso.CITAS_OTRA_PERSONA) == "C"


def puede_actuar_por(usuario, afiliado_id, *, delegacion_vigente: bool = False) -> bool:
    """¿Puede `usuario` operar la agenda de `afiliado_id`? (RT-08: los topes se aplican al atendido, no a quien opera)."""
    if not puede_reservar_en_linea(usuario):
        return False
    return usuario.id == afiliado_id or (delegacion_vigente and nivel(usuario, Permiso.CITAS_OTRA_PERSONA) == "C")


def puede_apelar(usuario, afiliado_id) -> bool:
    """Solo el propio titular apela sus restricciones."""
    return puede_reservar_en_linea(usuario) and usuario.id == afiliado_id


def puede_registrar_atencion(usuario, turno, *, relacion_asistencial: bool = False) -> bool:
    """Recepción de la misma unidad (o el profesional con relación asistencial) registra asistió / no asistió."""
    if not _activo(usuario) or usuario.establecimiento_id is None:
        return False
    misma_unidad = usuario.establecimiento_id == turno.establecimiento_id
    if usuario.rol == Rol.RECEPCION:
        return misma_unidad
    if usuario.rol == Rol.PROFESIONAL:
        return misma_unidad and relacion_asistencial
    return False


def puede_resolver_apelacion(usuario) -> bool:
    """Una persona de la unidad (recepción) resuelve; el afiliado nunca resuelve su propia apelación."""
    return _es(usuario, Rol.RECEPCION) and usuario.establecimiento_id is not None


def puede_ver_agenda_unidad(usuario, establecimiento_id) -> bool:
    """Recepción, profesional y administración de oferta ven la agenda de SU unidad (nivel U)."""
    if not _activo(usuario) or usuario.establecimiento_id is None:
        return False
    return nivel(usuario, Permiso.AGENDA_UNIDAD) == "U" and usuario.establecimiento_id == establecimiento_id


def puede_consultar_contacto(usuario) -> str:
    """Nivel con el que se consultan datos de contacto de afiliados: "M" (enmascarado) para recepción y soporte."""
    if not _es(usuario, Rol.RECEPCION, Rol.SOPORTE):
        return ""
    return nivel(usuario, Permiso.DATOS_CONTACTO)


def puede_ver_contacto_completo(usuario, *, justificacion_registrada: bool) -> bool:
    """Los datos completos solo con justificación registrada, y solo para recepción (el soporte nunca los ve completos)."""
    return justificacion_registrada and _es(usuario, Rol.RECEPCION)


def puede_ver_auditoria(usuario) -> bool:
    """Registros de auditoría: auditor y DPD, solo lectura."""
    return nivel(usuario, Permiso.REGISTROS_AUDITORIA) == "L"


def puede_crear_delegacion(usuario) -> bool:
    return puede_reservar_en_linea(usuario)


def puede_revocar_delegacion(usuario, delegacion) -> bool:
    """Revoca el titular o el propio representante; nadie más."""
    return puede_reservar_en_linea(usuario) and usuario.id in (delegacion.titular_id, delegacion.representante_id)


def puede_solicitar_derechos(usuario) -> bool:
    """Las solicitudes de derechos (acceso, rectificación, eliminación, oposición) son del titular."""
    return puede_reservar_en_linea(usuario)


# --- Enmascaramiento (soporte y recepción ven datos enmascarados) ------------------------------------------------------------


def enmascarar_cedula(cedula: str) -> str:
    return f"{cedula[:3]}*****{cedula[-2:]}" if len(cedula) == 10 else "**********"


def enmascarar_correo(correo: str) -> str:
    if "@" not in correo:
        return ""
    local, dominio = correo.split("@", 1)
    return f"{local[:1]}***@{dominio}"


def enmascarar_telefono(telefono: str) -> str:
    return f"{telefono[:3]}****{telefono[-2:]}" if len(telefono) >= 7 else "****"

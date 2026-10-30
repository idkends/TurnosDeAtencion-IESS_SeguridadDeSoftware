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

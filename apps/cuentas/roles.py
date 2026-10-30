"""Roles y estados de cuenta como enumeraciones simples (sin ORM), para que `politicas.py` siga siendo puro."""
from enum import StrEnum


class Rol(StrEnum):
    AFILIADO = "AFILIADO"
    REPRESENTANTE = "REPRESENTANTE"
    RECEPCION = "RECEPCION"
    PROFESIONAL = "PROFESIONAL"
    SOPORTE = "SOPORTE"
    ADMIN_OFERTA = "ADMIN_OFERTA"
    ADMIN_TI = "ADMIN_TI"
    AUDITOR_DPD = "AUDITOR_DPD"


class EstadoCuenta(StrEnum):
    PENDIENTE_ACTIVACION = "PENDIENTE_ACTIVACION"
    ACTIVA = "ACTIVA"
    SUSPENDIDA = "SUSPENDIDA"
    BAJA = "BAJA"


ROL_ETIQUETAS = {
    Rol.AFILIADO: "Afiliado",
    Rol.REPRESENTANTE: "Representante",
    Rol.RECEPCION: "Recepción",
    Rol.PROFESIONAL: "Profesional sanitario",
    Rol.SOPORTE: "Soporte",
    Rol.ADMIN_OFERTA: "Administración de oferta",
    Rol.ADMIN_TI: "Administrador de TI",
    Rol.AUDITOR_DPD: "Auditor y DPD",
}
ESTADO_ETIQUETAS = {
    EstadoCuenta.PENDIENTE_ACTIVACION: "Pendiente de activación",
    EstadoCuenta.ACTIVA: "Activa",
    EstadoCuenta.SUSPENDIDA: "Suspendida",
    EstadoCuenta.BAJA: "Baja",
}

ROLES_CIUDADANOS = frozenset({Rol.AFILIADO, Rol.REPRESENTANTE})
ROLES_FUNCIONARIO = frozenset(r for r in Rol if r not in ROLES_CIUDADANOS)

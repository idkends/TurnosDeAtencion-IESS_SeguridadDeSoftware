"""Autorización centralizada (§4.3, R06, R15, CA-01, CA-07, CA-09): funciones puras, deniega por defecto."""
import uuid
from dataclasses import dataclass

import pytest

from apps.cuentas import politicas
from apps.cuentas.politicas import Permiso, Rol


@dataclass
class U:
    rol: str
    id: uuid.UUID = None
    estado: str = "ACTIVA"
    establecimiento_id: uuid.UUID | None = None
    is_authenticated: bool = True

    def __post_init__(self):
        self.id = self.id or uuid.uuid4()


@dataclass
class T:
    afiliado_id: uuid.UUID
    establecimiento_id: uuid.UUID


UNIDAD_A, UNIDAD_B = uuid.uuid4(), uuid.uuid4()


@pytest.fixture
def turno():
    return T(afiliado_id=uuid.uuid4(), establecimiento_id=UNIDAD_A)


def test_la_matriz_del_informe_para_cada_rol():
    esperado = {
        Rol.AFILIADO: {Permiso.AGENDA_PROPIA: "S", Permiso.DATOS_CONTACTO: "S"},
        Rol.REPRESENTANTE: {Permiso.AGENDA_PROPIA: "S", Permiso.CITAS_OTRA_PERSONA: "C", Permiso.DATOS_CONTACTO: "C"},
        Rol.RECEPCION: {Permiso.CITAS_OTRA_PERSONA: "C", Permiso.AGENDA_UNIDAD: "U", Permiso.DATOS_CONTACTO: "M"},
        Rol.PROFESIONAL: {Permiso.AGENDA_UNIDAD: "U", Permiso.DATOS_CONTACTO: "M", Permiso.DATOS_CLINICOS: "C"},
        Rol.SOPORTE: {Permiso.DATOS_CONTACTO: "M", Permiso.REGISTROS_AUDITORIA: "T"},
        Rol.ADMIN_OFERTA: {Permiso.AGENDA_UNIDAD: "U", Permiso.CONFIGURAR_OFERTA: "S"},
        Rol.ADMIN_TI: {Permiso.CONFIGURAR_OFERTA: "I"},
        Rol.AUDITOR_DPD: {Permiso.AGENDA_UNIDAD: "L", Permiso.DATOS_CONTACTO: "C", Permiso.REGISTROS_AUDITORIA: "L", Permiso.DESCARGA_MASIVA: "C"},
    }
    for rol, permisos in esperado.items():
        for permiso in Permiso:
            assert politicas.nivel(U(rol), permiso) == permisos.get(permiso, ""), (rol, permiso)


def test_niguna_cuenta_no_activa_tiene_permisos():
    for estado in ("PENDIENTE_ACTIVACION", "SUSPENDIDA", "BAJA"):
        assert politicas.nivel(U(Rol.ADMIN_OFERTA, estado=estado), Permiso.CONFIGURAR_OFERTA) == ""


def test_solo_el_auditor_ve_registros_de_auditoria_en_lectura_y_el_soporte_solo_tecnicos():
    assert politicas.nivel(U(Rol.AUDITOR_DPD), Permiso.REGISTROS_AUDITORIA) == "L"
    assert politicas.nivel(U(Rol.SOPORTE), Permiso.REGISTROS_AUDITORIA) == "T"
    assert all(politicas.nivel(U(r), Permiso.REGISTROS_AUDITORIA) == "" for r in (Rol.AFILIADO, Rol.REPRESENTANTE, Rol.RECEPCION, Rol.PROFESIONAL, Rol.ADMIN_OFERTA, Rol.ADMIN_TI))


def test_nadie_tiene_datos_clinicos_en_el_portal_salvo_el_profesional_condicionado():
    for rol in Rol:
        assert politicas.nivel(U(rol), Permiso.DATOS_CLINICOS) == ("C" if rol == Rol.PROFESIONAL else "")


def test_el_afiliado_ve_su_propio_turno(turno):
    dueno = U(Rol.AFILIADO, id=turno.afiliado_id)
    assert politicas.puede_ver_turno(dueno, turno)


def test_CA07_otro_afiliado_no_ve_el_turno_ajeno(turno):
    assert not politicas.puede_ver_turno(U(Rol.AFILIADO), turno)
    assert not politicas.puede_ver_turno(U(Rol.REPRESENTANTE), turno)  # sin delegación


def test_CA01_el_representante_solo_ve_el_turno_con_delegacion_vigente(turno):
    rep = U(Rol.REPRESENTANTE)
    assert not politicas.puede_ver_turno(rep, turno, delegacion_vigente=False)
    assert politicas.puede_ver_turno(rep, turno, delegacion_vigente=True)


def test_un_afiliado_con_delegacion_no_obtiene_acceso_por_ser_afiliado_solamente(turno):
    # La delegación solo habilita a quien la matriz permite (representante); un rol sin ese permiso no la hereda.
    assert not politicas.puede_ver_turno(U(Rol.AFILIADO), turno, delegacion_vigente=True)


def test_CA09_recepcion_solo_ve_turnos_de_su_unidad(turno):
    assert politicas.puede_ver_turno(U(Rol.RECEPCION, establecimiento_id=UNIDAD_A), turno)
    assert not politicas.puede_ver_turno(U(Rol.RECEPCION, establecimiento_id=UNIDAD_B), turno)
    assert not politicas.puede_ver_turno(U(Rol.RECEPCION, establecimiento_id=None), turno)  # sin unidad: denegado


def test_el_profesional_necesita_relacion_asistencial_y_su_unidad(turno):
    prof = U(Rol.PROFESIONAL, establecimiento_id=UNIDAD_A)
    assert not politicas.puede_ver_turno(prof, turno)
    assert politicas.puede_ver_turno(prof, turno, relacion_asistencial=True)
    otra = U(Rol.PROFESIONAL, establecimiento_id=UNIDAD_B)
    assert not politicas.puede_ver_turno(otra, turno, relacion_asistencial=True)


def test_soporte_ti_y_auditoria_no_ven_fichas_individuales(turno):
    for rol in (Rol.SOPORTE, Rol.ADMIN_TI, Rol.AUDITOR_DPD):
        assert not politicas.puede_ver_turno(U(rol, establecimiento_id=UNIDAD_A), turno, delegacion_vigente=True, relacion_asistencial=True), rol


def test_deniega_por_defecto_ante_anonimo_none_o_cuenta_inactiva(turno):
    assert not politicas.puede_ver_turno(None, turno)
    assert not politicas.puede_ver_turno(U(Rol.AFILIADO, id=turno.afiliado_id, is_authenticated=False), turno)
    assert not politicas.puede_ver_turno(U(Rol.AFILIADO, id=turno.afiliado_id, estado="SUSPENDIDA"), turno)
    assert not politicas.puede_ver_turno(U("ROL_INVENTADO"), turno)


def test_requiere_mfa_funcionarios_siempre_y_ciudadanos_si_lo_activaron():
    for rol in politicas.ROLES_FUNCIONARIO:
        assert politicas.requiere_mfa(_con_totp(rol, False))
        assert politicas.debe_configurar_mfa(_con_totp(rol, False)) and not politicas.debe_configurar_mfa(_con_totp(rol, True))
    assert not politicas.requiere_mfa(_con_totp(Rol.AFILIADO, False))
    assert politicas.requiere_mfa(_con_totp(Rol.AFILIADO, True))


def _con_totp(rol, confirmado):
    u = U(rol)
    u.totp_confirmado = confirmado
    return u


def test_gestionar_sesiones_solo_las_propias():
    yo = U(Rol.AFILIADO)
    assert politicas.puede_gestionar_sesion(yo, yo.id)
    assert not politicas.puede_gestionar_sesion(yo, uuid.uuid4())
    assert not politicas.puede_gestionar_sesion(U(Rol.AFILIADO, estado="BAJA"), yo.id)


def test_operacion_sensible_reglas_de_inactividad_y_mfa_reciente():
    ahora = 1_000_000.0
    f = politicas.sesion_apta_para_operacion_sensible
    assert f(ahora_ts=ahora, ultima_actividad_ts=ahora - 3599, mfa_en_ts=ahora - 599) == ""
    assert f(ahora_ts=ahora, ultima_actividad_ts=ahora - 3601, mfa_en_ts=ahora - 10) == "INACTIVIDAD"
    assert f(ahora_ts=ahora, ultima_actividad_ts=ahora - 10, mfa_en_ts=ahora - 601) == "MFA_NO_RECIENTE"
    assert f(ahora_ts=ahora, ultima_actividad_ts=None, mfa_en_ts=ahora) == "INACTIVIDAD"
    assert f(ahora_ts=ahora, ultima_actividad_ts=ahora, mfa_en_ts=None) == "MFA_NO_RECIENTE"

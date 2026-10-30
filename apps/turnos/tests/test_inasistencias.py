"""Política de inasistencias (§3.5, RT-06, CA-14, T17): aviso, tope reducido, bloqueo, justificación, apelación y exenciones."""
import uuid
from datetime import timedelta

import pytest

from apps.auditoria.modelos import EventoAuditoria
from apps.turnos import servicios
from apps.turnos.modelos import Apelacion, Restriccion, Turno
from config import reloj
from tests.ayudas import franja_futura

pytestmark = pytest.mark.django_db


def _turno_confirmado(afiliado, est, esp, dias=1, hora=9):
    """Reserva y confirma; luego adelanta el reloj hasta pasada la hora del turno para poder registrar la inasistencia."""
    f = franja_futura(est, esp, dias=dias, hora=hora)
    t, _ = servicios.reservar(solicitante=afiliado, franja_id=f.id, motivo="", clave_idempotencia=uuid.uuid4().hex)
    servicios.confirmar_turno(afiliado, t.id)
    return t, f


def _no_asistio(recepcion, turno, franja):
    reloj.avanzar(max(timedelta(0), franja.inicio - reloj.ahora()) + timedelta(minutes=5))
    return servicios.registrar_atencion(recepcion, turno.id, "NO_ASISTIO")


@pytest.fixture
def recepcion(oferta, funcionario):
    return funcionario("RECEPCION", oferta["est1"])


def _vigentes(afiliado):
    return list(Restriccion.objects.filter(afiliado=afiliado, revertida_en__isnull=True))


def test_primera_inasistencia_avisa_y_reduce_el_tope_a_1_turno_activo(oferta, ciudadano, recepcion, correo_enviado):
    afiliado = ciudadano()
    t, f = _turno_confirmado(afiliado, oferta["est1"], oferta["esp1"])
    correo_enviado.clear()
    vista = _no_asistio(recepcion, t, f)
    assert vista.estado == "NO_ASISTIO"
    (r,) = _vigentes(afiliado)
    assert r.tipo == "TOPE_REDUCIDO" and r.causa == "INASISTENCIA_INJUSTIFICADA" and r.fin - reloj.ahora() > timedelta(days=89)
    aviso = [m for m in correo_enviado if m.plantilla == "inasistencia_registrada"]
    assert aviso and "72 horas" in aviso[0].cuerpo and "presencial y telefónica" in aviso[0].cuerpo
    assert "Medicina" not in aviso[0].cuerpo  # sin especialidad
    # Con el tope en 1, una segunda cita ACTIVA en otra especialidad ya no se puede reservar en línea…
    _turno_ok = servicios.reservar(solicitante=afiliado, franja_id=franja_futura(oferta["est1"], oferta["esp2"], dias=3).id, motivo="", clave_idempotencia=uuid.uuid4().hex)
    assert _turno_ok[0].estado == "RESERVADO"  # …la primera sí (no tenía ninguna activa)
    with pytest.raises(servicios.ReglaRechazada) as error:
        servicios.reservar(solicitante=afiliado, franja_id=franja_futura(oferta["est1"], oferta["esp3"], dias=4).id, motivo="", clave_idempotencia=uuid.uuid4().hex)
    assert error.value.codigo == "TOPE_DE_CITAS"


def test_CA14_dos_inasistencias_consecutivas_bloquean_la_reserva_en_linea_14_dias_con_causa_y_fecha(oferta, ciudadano, recepcion):
    afiliado = ciudadano()
    for dia in (1, 3):
        t, f = _turno_confirmado(afiliado, oferta["est1"], oferta["esp1"], dias=dia)
        _no_asistio(recepcion, t, f)
    bloqueo = Restriccion.objects.get(afiliado=afiliado, tipo="BLOQUEO_RESERVA")
    assert bloqueo.especialidad_id == oferta["esp1"].id and timedelta(days=13) < bloqueo.fin - reloj.ahora() <= timedelta(days=14)
    with pytest.raises(servicios.ReglaRechazada) as error:
        servicios.reservar(solicitante=afiliado, franja_id=franja_futura(oferta["est1"], oferta["esp1"], dias=10).id, motivo="", clave_idempotencia=uuid.uuid4().hex)
    # El afiliado ve la CAUSA, la FECHA de fin y el canal presencial (no queda bloqueado sin poder reclamar).
    assert error.value.codigo == "RESTRICCION_VIGENTE" and error.value.ofrecer_presencial and error.value.hasta == bloqueo.fin
    assert "bloqueada hasta el" in error.value.mensaje and "presencial" in error.value.mensaje
    visibles = servicios.restricciones_de(afiliado)
    assert {r.tipo for r in visibles} == {"BLOQUEO_RESERVA", "TOPE_REDUCIDO"} and all(r.fin and r.causa_texto for r in visibles)


def test_el_bloqueo_es_solo_de_esa_especialidad(oferta, ciudadano, recepcion):
    afiliado = ciudadano()
    for dia in (1, 3):
        t, f = _turno_confirmado(afiliado, oferta["est1"], oferta["esp1"], dias=dia)
        _no_asistio(recepcion, t, f)
    t, _ = servicios.reservar(solicitante=afiliado, franja_id=franja_futura(oferta["est1"], oferta["esp2"], dias=10).id, motivo="", clave_idempotencia=uuid.uuid4().hex)
    assert t.estado == "RESERVADO"  # otra especialidad: sin bloqueo (aunque aplique el tope reducido)


def test_una_inasistencia_entre_asistencias_no_son_consecutivas(oferta, ciudadano, recepcion):
    afiliado = ciudadano()
    t1, f1 = _turno_confirmado(afiliado, oferta["est1"], oferta["esp1"], dias=1)
    _no_asistio(recepcion, t1, f1)
    t2, f2 = _turno_confirmado(afiliado, oferta["est1"], oferta["esp1"], dias=3)
    reloj.avanzar(f2.inicio - reloj.ahora() + timedelta(minutes=5))
    servicios.registrar_atencion(recepcion, t2.id, "ATENDIDO")  # asistió: rompe la cadena
    t3, f3 = _turno_confirmado(afiliado, oferta["est1"], oferta["esp1"], dias=6)
    _no_asistio(recepcion, t3, f3)
    assert not Restriccion.objects.filter(afiliado=afiliado, tipo="BLOQUEO_RESERVA").exists()


def test_tres_inasistencias_en_90_dias_piden_revision_humana_sin_sancion_automatica_adicional(oferta, ciudadano, recepcion):
    afiliado = ciudadano()
    for esp, dia in ((oferta["esp1"], 1), (oferta["esp2"], 3), (oferta["esp3"], 5)):
        t, f = _turno_confirmado(afiliado, oferta["est1"], esp, dias=dia)
        _no_asistio(recepcion, t, f)
    assert EventoAuditoria.objects.filter(accion="INASISTENCIAS_REVISION_HUMANA", actor_id=afiliado.id).count() == 1
    assert {r.tipo for r in _vigentes(afiliado)} == {"TOPE_REDUCIDO"}  # distintas especialidades: ni siquiera bloqueo


def test_T17_justificar_dentro_de_72_horas_revierte_la_inasistencia_y_la_restriccion(oferta, ciudadano, recepcion, correo_enviado):
    afiliado = ciudadano()
    t, f = _turno_confirmado(afiliado, oferta["est1"], oferta["esp1"])
    _no_asistio(recepcion, t, f)
    assert _vigentes(afiliado)
    correo_enviado.clear()
    reloj.avanzar(timedelta(hours=71))
    vista = servicios.registrar_atencion(recepcion, t.id, "JUSTIFICADO", justificacion_tipo="DOCUMENTO")
    assert vista.estado == "JUSTIFICADO" and _vigentes(afiliado) == []
    assert [m.plantilla for m in correo_enviado] == ["restriccion_levantada"]
    assert Turno.objects.get(pk=t.id).justificacion_tipo == "DOCUMENTO"


def test_la_justificacion_fuera_de_las_72_horas_se_rechaza(oferta, ciudadano, recepcion):
    afiliado = ciudadano()
    t, f = _turno_confirmado(afiliado, oferta["est1"], oferta["esp1"])
    _no_asistio(recepcion, t, f)
    reloj.avanzar(timedelta(hours=73))
    with pytest.raises(servicios.ReglaRechazada) as error:
        servicios.registrar_atencion(recepcion, t.id, "JUSTIFICADO", justificacion_tipo="DECLARACION")
    assert error.value.codigo == "JUSTIFICACION_FUERA_DE_PLAZO" and _vigentes(afiliado)


def test_justificar_una_de_dos_inasistencias_levanta_el_bloqueo(oferta, ciudadano, recepcion):
    afiliado = ciudadano()
    turnos = []
    for dia in (1, 3):
        t, f = _turno_confirmado(afiliado, oferta["est1"], oferta["esp1"], dias=dia)
        _no_asistio(recepcion, t, f)
        turnos.append(t)
    assert Restriccion.objects.filter(afiliado=afiliado, tipo="BLOQUEO_RESERVA", revertida_en__isnull=True).exists()
    servicios.registrar_atencion(recepcion, turnos[1].id, "JUSTIFICADO", justificacion_tipo="DOCUMENTO")
    assert not Restriccion.objects.filter(afiliado=afiliado, tipo="BLOQUEO_RESERVA", revertida_en__isnull=True).exists()
    assert Restriccion.objects.filter(afiliado=afiliado, tipo="TOPE_REDUCIDO", revertida_en__isnull=True).exists()  # la otra sigue contando


def test_las_restricciones_expiran_solas_a_su_fecha_de_fin(oferta, ciudadano, recepcion):
    afiliado = ciudadano()
    t, f = _turno_confirmado(afiliado, oferta["est1"], oferta["esp1"])
    _no_asistio(recepcion, t, f)
    assert servicios.restricciones_de(afiliado)
    reloj.avanzar(timedelta(days=91))
    assert servicios.restricciones_de(afiliado) == []


def test_RT11_los_grupos_de_atencion_prioritaria_no_reciben_restricciones_automaticas(oferta, ciudadano, recepcion):
    afiliado = ciudadano(atencion_prioritaria=True)
    for dia in (1, 3):
        t, f = _turno_confirmado(afiliado, oferta["est1"], oferta["esp1"], dias=dia)
        _no_asistio(recepcion, t, f)
    assert _vigentes(afiliado) == []
    reservado, _ = servicios.reservar(solicitante=afiliado, franja_id=franja_futura(oferta["est1"], oferta["esp1"], dias=10).id, motivo="", clave_idempotencia=uuid.uuid4().hex)
    assert reservado.estado == "RESERVADO"


def test_la_atencion_presencial_nunca_se_bloquea_recepcion_registra_aun_con_restricciones(oferta, ciudadano, recepcion):
    afiliado = ciudadano()
    for dia in (1, 3):
        t, f = _turno_confirmado(afiliado, oferta["est1"], oferta["esp1"], dias=dia)
        _no_asistio(recepcion, t, f)
    assert Restriccion.objects.filter(afiliado=afiliado, tipo="BLOQUEO_RESERVA").exists()
    # La unidad atiende y registra sin que la restricción en línea se lo impida (el canal presencial siempre existe).
    f4 = franja_futura(oferta["est1"], oferta["esp2"], dias=8)
    t4 = Turno.objects.create(afiliado=afiliado, solicitante=afiliado, especialidad=oferta["esp2"], franja=f4, estado="CONFIRMADO", inicio=f4.inicio, fin=f4.fin)
    reloj.avanzar(f4.inicio - reloj.ahora() + timedelta(minutes=1))
    assert servicios.registrar_atencion(recepcion, t4.id, "ATENDIDO").estado == "ATENDIDO"


# --- Quién puede registrar ----------------------------------------------------------------------------------------------------------------------


def test_el_afiliado_nunca_marca_sus_propias_inasistencias(oferta, ciudadano):
    afiliado = ciudadano()
    t, f = _turno_confirmado(afiliado, oferta["est1"], oferta["esp1"])
    reloj.avanzar(f.inicio - reloj.ahora() + timedelta(minutes=5))
    with pytest.raises(servicios.AccesoDenegado):
        servicios.registrar_atencion(afiliado, t.id, "ATENDIDO")  # ni siquiera "asistió" para evitar la sanción
    with pytest.raises(servicios.AccesoDenegado):
        servicios.registrar_atencion(afiliado, t.id, "NO_ASISTIO")
    assert Turno.objects.get(pk=t.id).estado == "CONFIRMADO"


def test_CA09_recepcion_de_otra_unidad_recibe_denegacion_y_queda_auditado(oferta, ciudadano, funcionario):
    afiliado = ciudadano()
    t, f = _turno_confirmado(afiliado, oferta["est1"], oferta["esp1"])
    reloj.avanzar(f.inicio - reloj.ahora() + timedelta(minutes=5))
    ajena = funcionario("RECEPCION", oferta["est2"])
    with pytest.raises(servicios.AccesoDenegado):
        servicios.registrar_atencion(ajena, t.id, "NO_ASISTIO")
    assert Turno.objects.get(pk=t.id).estado == "CONFIRMADO"
    assert EventoAuditoria.objects.filter(accion="ACCESO_DENEGADO", actor_id=ajena.id, resultado="DENEGADO").exists()


@pytest.mark.parametrize("rol", ["SOPORTE", "ADMIN_OFERTA", "ADMIN_TI", "AUDITOR_DPD", "PROFESIONAL"])
def test_solo_recepcion_registra_asistencia_en_esta_etapa(oferta, ciudadano, funcionario, rol):
    afiliado = ciudadano()
    t, f = _turno_confirmado(afiliado, oferta["est1"], oferta["esp1"])
    reloj.avanzar(f.inicio - reloj.ahora() + timedelta(minutes=5))
    with pytest.raises(servicios.AccesoDenegado):
        servicios.registrar_atencion(funcionario(rol, oferta["est1"]), t.id, "NO_ASISTIO")


def test_no_se_registra_inasistencia_antes_de_la_hora_ni_en_turnos_no_confirmados(oferta, ciudadano, recepcion):
    afiliado = ciudadano()
    t, f = _turno_confirmado(afiliado, oferta["est1"], oferta["esp1"], dias=5)
    with pytest.raises(servicios.ReglaRechazada):
        servicios.registrar_atencion(recepcion, t.id, "NO_ASISTIO")  # todavía no es la hora
    otro = ciudadano()
    reservado, _ = servicios.reservar(solicitante=otro, franja_id=franja_futura(oferta["est1"], oferta["esp1"], dias=1, hora=11).id, motivo="", clave_idempotencia=uuid.uuid4().hex)
    reloj.avanzar(timedelta(days=2))
    with pytest.raises(servicios.ReglaRechazada):
        servicios.registrar_atencion(recepcion, reservado.id, "NO_ASISTIO")  # nunca confirmó


def test_registrar_es_idempotente_y_cada_cambio_queda_auditado(oferta, ciudadano, recepcion):
    afiliado = ciudadano()
    t, f = _turno_confirmado(afiliado, oferta["est1"], oferta["esp1"])
    _no_asistio(recepcion, t, f)
    servicios.registrar_atencion(recepcion, t.id, "NO_ASISTIO")  # repetir no duplica
    eventos = EventoAuditoria.objects.filter(accion="INASISTENCIA_REGISTRADA")
    assert eventos.count() == 1 and eventos[0].actor_id == recepcion.id
    assert eventos[0].detalle["estado_previo"] == "CONFIRMADO" and eventos[0].detalle["estado_nuevo"] == "NO_ASISTIO"
    assert Restriccion.objects.filter(afiliado=afiliado, tipo="TOPE_REDUCIDO").count() == 1
    with pytest.raises(servicios.ReglaRechazada):
        servicios.registrar_atencion(recepcion, t.id, "ATENDIDO")  # no se reescribe una inasistencia (solo adición)


def test_resultado_invalido_y_turno_inexistente(oferta, recepcion):
    with pytest.raises(servicios.ParametroInvalido):
        servicios.registrar_atencion(recepcion, uuid.uuid4(), "INVENTADO")
    with pytest.raises(servicios.NoEncontrado):
        servicios.registrar_atencion(recepcion, uuid.uuid4(), "ATENDIDO")


# --- Apelación ------------------------------------------------------------------------------------------------------------------------------------


def _con_restriccion(oferta, ciudadano, recepcion):
    afiliado = ciudadano()
    t, f = _turno_confirmado(afiliado, oferta["est1"], oferta["esp1"])
    _no_asistio(recepcion, t, f)
    return afiliado, t


def test_apelar_crea_una_apelacion_con_respuesta_en_5_dias_habiles(oferta, ciudadano, recepcion, correo_enviado):
    afiliado, t = _con_restriccion(oferta, ciudadano, recepcion)
    assert servicios.detalle_turno(afiliado, t.id).puede_apelar
    apelacion = servicios.apelar_restriccion(afiliado, t.id, "Estuve hospitalizado ese día <script>alert(1)</script>")
    assert apelacion.estado == "PENDIENTE" and apelacion.texto.startswith("Estuve hospitalizado")
    dias = (apelacion.responder_antes_de - apelacion.creada).days
    assert 5 <= dias <= 7  # 5 días hábiles = entre 5 y 7 días naturales
    assert apelacion.responder_antes_de.weekday() < 5 and EventoAuditoria.objects.filter(accion="APELACION_CREADA").count() == 1
    assert any(m.plantilla == "apelacion_recibida" for m in correo_enviado)


def test_apelar_dos_veces_devuelve_la_misma_apelacion(oferta, ciudadano, recepcion):
    afiliado, t = _con_restriccion(oferta, ciudadano, recepcion)
    a1 = servicios.apelar_restriccion(afiliado, t.id, "Primera")
    a2 = servicios.apelar_restriccion(afiliado, t.id, "Segunda")
    assert a1.id == a2.id and Apelacion.objects.count() == 1


@pytest.mark.parametrize("texto", ["", "   ", "x" * 501])
def test_el_texto_de_la_apelacion_es_limitado(oferta, ciudadano, recepcion, texto):
    afiliado, t = _con_restriccion(oferta, ciudadano, recepcion)
    with pytest.raises(servicios.ReglaRechazada) as error:
        servicios.apelar_restriccion(afiliado, t.id, texto)
    assert error.value.codigo == "TEXTO_INVALIDO"


def test_solo_el_propio_titular_apela_y_el_turno_ajeno_da_404(oferta, ciudadano, recepcion):
    afiliado, t = _con_restriccion(oferta, ciudadano, recepcion)
    with pytest.raises(servicios.NoEncontrado):
        servicios.apelar_restriccion(ciudadano(), t.id, "Intento ajeno")
    with pytest.raises(servicios.NoEncontrado):
        servicios.apelar_restriccion(recepcion, t.id, "El personal tampoco apela por el afiliado")


def test_sin_restriccion_vigente_no_hay_nada_que_apelar(oferta, ciudadano):
    afiliado = ciudadano()
    t, _ = servicios.reservar(solicitante=afiliado, franja_id=franja_futura(oferta["est1"], oferta["esp1"]).id, motivo="", clave_idempotencia=uuid.uuid4().hex)
    with pytest.raises(servicios.ReglaRechazada) as error:
        servicios.apelar_restriccion(afiliado, t.id, "No tengo restricción")
    assert error.value.codigo == "SIN_RESTRICCION"


def test_una_persona_de_la_unidad_resuelve_la_apelacion_y_aceptarla_levanta_la_restriccion(oferta, ciudadano, recepcion, correo_enviado):
    afiliado, t = _con_restriccion(oferta, ciudadano, recepcion)
    apelacion = servicios.apelar_restriccion(afiliado, t.id, "Justificación")
    resuelta = servicios.resolver_apelacion(recepcion, apelacion.id, aceptar=True, motivo="Certificado presentado")
    assert resuelta.estado == "ACEPTADA" and resuelta.resuelta_por_id == recepcion.id
    assert _vigentes(afiliado) == [] and any(m.plantilla == "apelacion_resuelta" for m in correo_enviado)
    assert servicios.resolver_apelacion(recepcion, apelacion.id, aceptar=False).estado == "ACEPTADA"  # ya resuelta: no cambia


def test_rechazar_la_apelacion_mantiene_la_restriccion(oferta, ciudadano, recepcion):
    afiliado, t = _con_restriccion(oferta, ciudadano, recepcion)
    apelacion = servicios.apelar_restriccion(afiliado, t.id, "Justificación")
    assert servicios.resolver_apelacion(recepcion, apelacion.id, aceptar=False).estado == "RECHAZADA"
    assert _vigentes(afiliado)


def test_el_afiliado_no_resuelve_su_propia_apelacion(oferta, ciudadano, recepcion):
    afiliado, t = _con_restriccion(oferta, ciudadano, recepcion)
    apelacion = servicios.apelar_restriccion(afiliado, t.id, "Justificación")
    with pytest.raises(servicios.AccesoDenegado):
        servicios.resolver_apelacion(afiliado, apelacion.id, aceptar=True)
    assert Apelacion.objects.get(pk=apelacion.id).estado == "PENDIENTE"

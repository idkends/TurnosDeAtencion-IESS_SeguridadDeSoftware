"""Ciclo de vida: confirmar (24 h), cancelar (2 h), deshacer (10 min), reprogramar (2 veces) y expiración (RT-05, RT-07)."""
import uuid
from datetime import timedelta

import pytest

from apps.auditoria.modelos import EventoAuditoria
from apps.integraciones import servicios as integraciones
from apps.turnos import servicios
from apps.turnos.modelos import EstadoFranja, Franja, Turno
from config import reloj
from tests.ayudas import franja_en, franja_futura

pytestmark = pytest.mark.django_db


def _reservar(usuario, franja):
    turno, _ = servicios.reservar(solicitante=usuario, franja_id=franja.id, motivo="", clave_idempotencia=uuid.uuid4().hex)
    return turno


def _franja_estado(f):
    return Franja.objects.get(pk=f.pk).estado


# --- Confirmar ---------------------------------------------------------------------------------------------------------------------------------


def test_confirmar_dentro_de_24_horas_y_es_idempotente(oferta, ciudadano, correo_enviado):
    usuario = ciudadano()
    turno = _reservar(usuario, franja_futura(oferta["est1"], oferta["esp1"]))
    correo_enviado.clear()
    confirmado = servicios.confirmar_turno(usuario, turno.id)
    assert confirmado.estado == "CONFIRMADO"
    assert [m.plantilla for m in correo_enviado] == ["cita_confirmada"] and "Tiene una cita el " in correo_enviado[0].cuerpo
    assert "Medicina" not in correo_enviado[0].cuerpo  # neutral
    assert servicios.confirmar_turno(usuario, turno.id).estado == "CONFIRMADO"  # repetir no hace nada raro
    assert len(correo_enviado) == 1 and EventoAuditoria.objects.filter(accion="TURNO_CONFIRMADO").count() == 1


def test_RT05_no_se_confirma_pasadas_las_24_horas(oferta, ciudadano):
    usuario = ciudadano()
    turno = _reservar(usuario, franja_futura(oferta["est1"], oferta["esp1"], dias=5))
    reloj.avanzar(timedelta(hours=24, minutes=1))
    with pytest.raises(servicios.ReglaRechazada) as error:
        servicios.confirmar_turno(usuario, turno.id)
    assert error.value.codigo == "NO_SE_PUEDE_CONFIRMAR"


def test_solo_se_confirma_desde_reservado(oferta, ciudadano):
    integraciones.agenda_memoria().caida = True
    usuario = ciudadano()
    turno = _reservar(usuario, franja_futura(oferta["est1"], oferta["esp1"]))  # PENDIENTE
    with pytest.raises(servicios.ReglaRechazada):
        servicios.confirmar_turno(usuario, turno.id)


# --- Expiración (tarea programada) -----------------------------------------------------------------------------------------------------------------


def test_RT05_sin_confirmar_en_24_horas_el_turno_expira_y_libera_el_cupo_con_aviso(oferta, ciudadano, correo_enviado):
    usuario = ciudadano()
    f = franja_futura(oferta["est1"], oferta["esp1"], dias=5)
    turno = _reservar(usuario, f)
    correo_enviado.clear()
    assert servicios.expirar_vencidos()["expirados"] == 0  # todavía no vence
    reloj.avanzar(timedelta(hours=24, minutes=1))
    assert servicios.expirar_vencidos()["expirados"] == 1
    assert Turno.objects.get(pk=turno.id).estado == "EXPIRADO" and _franja_estado(f) == EstadoFranja.LIBRE
    assert [m.plantilla for m in correo_enviado] == ["cita_expirada"]
    assert EventoAuditoria.objects.filter(accion="TURNO_EXPIRADO").count() == 1
    assert integraciones.agenda_memoria().cancelados == [str(turno.id)]  # la agenda también libera el cupo
    # El cupo liberado se puede volver a reservar, también por la misma persona.
    assert _reservar(usuario, f).estado == "RESERVADO"


def test_un_turno_confirmado_no_expira(oferta, ciudadano):
    usuario = ciudadano()
    turno = _reservar(usuario, franja_futura(oferta["est1"], oferta["esp1"], dias=5))
    servicios.confirmar_turno(usuario, turno.id)
    reloj.avanzar(timedelta(hours=30))
    assert servicios.expirar_vencidos()["expirados"] == 0 and Turno.objects.get(pk=turno.id).estado == "CONFIRMADO"


def test_la_expiracion_es_idempotente(oferta, ciudadano):
    _reservar(ciudadano(), franja_futura(oferta["est1"], oferta["esp1"], dias=5))
    reloj.avanzar(timedelta(hours=25))
    assert servicios.expirar_vencidos()["expirados"] == 1
    assert servicios.expirar_vencidos()["expirados"] == 0


def test_el_comando_de_tareas_ejecuta_expiracion_y_conciliacion(oferta, ciudadano, capsys):
    from django.core.management import call_command

    _reservar(ciudadano(), franja_futura(oferta["est1"], oferta["esp1"], dias=5))
    reloj.avanzar(timedelta(hours=25))
    call_command("expirar_turnos")
    assert "'expirados': 1" in capsys.readouterr().out
    call_command("conciliar_agenda")
    assert "revisados" in capsys.readouterr().out


# --- Cancelar ----------------------------------------------------------------------------------------------------------------------------------------


def test_cancelar_libera_el_cupo_de_inmediato_y_es_idempotente(oferta, ciudadano, correo_enviado):
    usuario = ciudadano()
    f = franja_futura(oferta["est1"], oferta["esp1"])
    turno = _reservar(usuario, f)
    cancelado = servicios.cancelar_turno(usuario, turno.id)
    assert cancelado.estado == "CANCELADO_AFILIADO" and _franja_estado(f) == EstadoFranja.LIBRE
    assert servicios.cancelar_turno(usuario, turno.id).estado == "CANCELADO_AFILIADO"
    assert EventoAuditoria.objects.filter(accion="TURNO_CANCELADO").count() == 1
    assert integraciones.agenda_memoria().cancelados == [str(turno.id)]
    assert [m.plantilla for m in correo_enviado].count("cita_cancelada") == 1
    assert _reservar(ciudadano(), f).estado == "RESERVADO"  # otra persona toma el cupo


def test_RT07_un_turno_confirmado_se_cancela_hasta_2_horas_antes(oferta, ciudadano):
    usuario = ciudadano()
    f_ok = franja_en(oferta["est1"], oferta["esp1"], timedelta(hours=2, minutes=5))
    turno = _reservar(usuario, f_ok)
    servicios.confirmar_turno(usuario, turno.id)
    reloj.avanzar(timedelta(minutes=10))  # ahora faltan menos de 2 horas
    with pytest.raises(servicios.ReglaRechazada) as error:
        servicios.cancelar_turno(usuario, turno.id)
    assert error.value.codigo == "CANCELACION_TARDIA" and error.value.ofrecer_presencial
    assert Turno.objects.get(pk=turno.id).estado == "CONFIRMADO"


def test_un_turno_reservado_sin_confirmar_se_puede_cancelar_aunque_falte_poco(oferta, ciudadano):
    usuario = ciudadano()
    turno = _reservar(usuario, franja_en(oferta["est1"], oferta["esp1"], timedelta(hours=1)))
    assert servicios.cancelar_turno(usuario, turno.id).estado == "CANCELADO_AFILIADO"


def test_no_se_cancela_un_turno_ya_atendido_o_expirado(oferta, ciudadano):
    usuario = ciudadano()
    turno = _reservar(usuario, franja_futura(oferta["est1"], oferta["esp1"]))
    Turno.objects.filter(pk=turno.id).update(estado="ATENDIDO")
    with pytest.raises(servicios.ReglaRechazada):
        servicios.cancelar_turno(usuario, turno.id)


# --- Deshacer la cancelación (10 minutos) ----------------------------------------------------------------------------------------------------------


def test_deshacer_la_cancelacion_dentro_de_10_minutos_si_el_cupo_sigue_libre(oferta, ciudadano):
    usuario = ciudadano()
    f = franja_futura(oferta["est1"], oferta["esp1"])
    turno = _reservar(usuario, f)
    servicios.confirmar_turno(usuario, turno.id)
    servicios.cancelar_turno(usuario, turno.id)
    assert servicios.detalle_turno(usuario, turno.id).puede_deshacer
    reloj.avanzar(timedelta(minutes=9))
    recuperado = servicios.deshacer_cancelacion(usuario, turno.id)
    assert recuperado.estado == "CONFIRMADO" and _franja_estado(f) == EstadoFranja.OCUPADA  # vuelve a su estado anterior
    assert EventoAuditoria.objects.filter(accion="CANCELACION_DESHECHA").count() == 1


def test_no_se_deshace_despues_de_10_minutos(oferta, ciudadano):
    usuario = ciudadano()
    turno = _reservar(usuario, franja_futura(oferta["est1"], oferta["esp1"]))
    servicios.cancelar_turno(usuario, turno.id)
    reloj.avanzar(timedelta(minutes=11))
    assert not servicios.detalle_turno(usuario, turno.id).puede_deshacer
    with pytest.raises(servicios.ReglaRechazada):
        servicios.deshacer_cancelacion(usuario, turno.id)


def test_no_se_deshace_si_otra_persona_ya_tomo_el_cupo(oferta, ciudadano):
    usuario, otra = ciudadano(), ciudadano()
    f = franja_futura(oferta["est1"], oferta["esp1"])
    turno = _reservar(usuario, f)
    servicios.cancelar_turno(usuario, turno.id)
    _reservar(otra, f)
    with pytest.raises(servicios.ReglaRechazada) as error:
        servicios.deshacer_cancelacion(usuario, turno.id)
    assert error.value.codigo == "CUPO_NO_DISPONIBLE" and Turno.objects.get(pk=turno.id).estado == "CANCELADO_AFILIADO"


def test_no_se_deshace_si_la_persona_ya_tiene_otro_turno_activo_en_la_especialidad(oferta, ciudadano):
    usuario = ciudadano()
    turno = _reservar(usuario, franja_futura(oferta["est1"], oferta["esp1"], hora=9))
    servicios.cancelar_turno(usuario, turno.id)
    _reservar(usuario, franja_futura(oferta["est1"], oferta["esp1"], hora=11))  # tomó otra franja
    with pytest.raises(servicios.ReglaRechazada) as error:
        servicios.deshacer_cancelacion(usuario, turno.id)  # la base (RT-01) lo impide
    assert error.value.codigo == "YA_TIENE_CITA_ESPECIALIDAD"


# --- Reprogramar ---------------------------------------------------------------------------------------------------------------------------------------


def test_reprogramar_mueve_el_turno_libera_la_anterior_y_reinicia_la_confirmacion(oferta, ciudadano):
    usuario = ciudadano()
    f1, f2 = franja_futura(oferta["est1"], oferta["esp1"], hora=9), franja_futura(oferta["est1"], oferta["esp1"], hora=11)
    turno = _reservar(usuario, f1)
    servicios.confirmar_turno(usuario, turno.id)
    nuevo, creado = servicios.reprogramar_turno(usuario, turno.id, f2.id, uuid.uuid4().hex)
    assert creado and nuevo.estado == "RESERVADO" and nuevo.reprogramaciones == 1  # deberá confirmar de nuevo
    assert _franja_estado(f1) == EstadoFranja.LIBRE and _franja_estado(f2) == EstadoFranja.OCUPADA
    assert Turno.objects.get(pk=turno.id).inicio == f2.inicio


def test_RT07_se_reprograma_como_maximo_2_veces(oferta, ciudadano):
    usuario = ciudadano()
    franjas = [franja_futura(oferta["est1"], oferta["esp1"], hora=h) for h in (8, 9, 10, 11)]
    turno = _reservar(usuario, franjas[0])
    servicios.reprogramar_turno(usuario, turno.id, franjas[1].id, uuid.uuid4().hex)
    servicios.reprogramar_turno(usuario, turno.id, franjas[2].id, uuid.uuid4().hex)
    with pytest.raises(servicios.ReglaRechazada) as error:
        servicios.reprogramar_turno(usuario, turno.id, franjas[3].id, uuid.uuid4().hex)
    assert error.value.codigo == "LIMITE_REPROGRAMACIONES" and _franja_estado(franjas[3]) == EstadoFranja.LIBRE


def test_reprogramar_es_idempotente_por_clave(oferta, ciudadano):
    usuario = ciudadano()
    f1, f2 = franja_futura(oferta["est1"], oferta["esp1"], hora=9), franja_futura(oferta["est1"], oferta["esp1"], hora=11)
    turno = _reservar(usuario, f1)
    clave = uuid.uuid4().hex
    servicios.reprogramar_turno(usuario, turno.id, f2.id, clave)
    otra, creado = servicios.reprogramar_turno(usuario, turno.id, f2.id, clave)
    assert not creado and Turno.objects.get(pk=turno.id).reprogramaciones == 1  # no cuenta dos veces


def test_no_se_reprograma_a_una_franja_ocupada_de_otra_especialidad_o_pasada(oferta, ciudadano):
    usuario, otra = ciudadano(), ciudadano()
    turno = _reservar(usuario, franja_futura(oferta["est1"], oferta["esp1"], hora=9))
    ocupada = franja_futura(oferta["est1"], oferta["esp1"], hora=10)
    _reservar(otra, ocupada)
    for destino in (ocupada, franja_futura(oferta["est1"], oferta["esp2"], hora=12), franja_futura(oferta["est1"], oferta["esp1"], dias=-1), None):
        franja_id = destino.id if destino else uuid.uuid4()
        with pytest.raises(servicios.ReglaRechazada):
            servicios.reprogramar_turno(usuario, turno.id, franja_id, uuid.uuid4().hex)
    assert Turno.objects.get(pk=turno.id).reprogramaciones == 0


def test_no_se_reprograma_con_menos_de_2_horas(oferta, ciudadano):
    usuario = ciudadano()
    turno = _reservar(usuario, franja_en(oferta["est1"], oferta["esp1"], timedelta(hours=1)))
    with pytest.raises(servicios.ReglaRechazada) as error:
        servicios.reprogramar_turno(usuario, turno.id, franja_futura(oferta["est1"], oferta["esp1"]).id, uuid.uuid4().hex)
    assert error.value.codigo == "REPROGRAMACION_TARDIA"


def test_reprogramar_no_permite_solapar_con_otro_turno_propio(oferta, ciudadano):
    usuario = ciudadano()
    _reservar(usuario, franja_futura(oferta["est1"], oferta["esp2"], hora=11, minuto=0))
    turno = _reservar(usuario, franja_futura(oferta["est1"], oferta["esp1"], hora=9))
    with pytest.raises(servicios.ReglaRechazada) as error:
        servicios.reprogramar_turno(usuario, turno.id, franja_futura(oferta["est2"], oferta["esp1"], hora=11, minuto=15).id, uuid.uuid4().hex)
    assert error.value.codigo == "SOLAPE_DE_HORARIO"

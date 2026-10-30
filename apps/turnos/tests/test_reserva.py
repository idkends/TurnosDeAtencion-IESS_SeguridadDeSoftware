"""Reserva (RT-01 a RT-12, Figura 2): CA-01, CA-02, CA-13, CA-15 y la validación en el orden del informe."""
import uuid
from datetime import timedelta

import pytest

from apps.auditoria.modelos import EventoAuditoria
from apps.delegaciones import servicios as delegaciones
from apps.integraciones import servicios as integraciones
from apps.turnos import servicios
from apps.turnos.modelos import EstadoFranja, Franja, SolicitudReserva, Turno
from config import reloj
from tests.ayudas import franja_futura, peticion

pytestmark = pytest.mark.django_db


def _clave():
    return uuid.uuid4().hex


def _reservar(usuario, franja, *, clave=None, **extra):
    return servicios.reservar(solicitante=usuario, franja_id=franja.id, motivo="CONTROL", clave_idempotencia=_clave() if clave is None else clave, **extra)


def test_una_reserva_valida_pasa_por_pendiente_y_queda_reservada(oferta, ciudadano, correo_enviado):
    usuario = ciudadano()
    f = franja_futura(oferta["est1"], oferta["esp1"])
    turno, creado = _reservar(usuario, f)
    assert creado and turno.estado == "RESERVADO"  # la agenda simulada confirmó
    assert turno.confirmar_antes_de - reloj.ahora() > timedelta(hours=23, minutes=59)  # RT-05: 24 h para confirmar
    assert Franja.objects.get(pk=f.pk).estado == EstadoFranja.OCUPADA
    assert [m.plantilla for m in correo_enviado] == ["reserva_creada"]
    assert correo_enviado[0].cuerpo == "Tiene una cita pendiente de confirmar. Ingrese al portal."  # neutral: sin especialidad
    assert EventoAuditoria.objects.filter(accion="RESERVA_CREADA").count() == 1 and EventoAuditoria.objects.filter(accion="TURNO_RESERVADO").count() == 1


def test_el_turno_nace_pendiente_y_solo_la_agenda_lo_pasa_a_reservado(oferta, ciudadano):
    integraciones.agenda_memoria().caida = True
    usuario = ciudadano()
    turno, _ = _reservar(usuario, franja_futura(oferta["est1"], oferta["esp1"]))
    assert turno.estado == "PENDIENTE" and turno.confirmar_antes_de is None


def test_CA02_segunda_reserva_en_la_misma_especialidad_se_rechaza_con_mensaje_generico(oferta, ciudadano):
    usuario = ciudadano()
    _reservar(usuario, franja_futura(oferta["est1"], oferta["esp1"], hora=9))
    otra = franja_futura(oferta["est1"], oferta["esp1"], hora=11)
    with pytest.raises(servicios.ReglaRechazada) as error:
        _reservar(usuario, otra)
    assert error.value.codigo == "YA_TIENE_CITA_ESPECIALIDAD" and error.value.mensaje == servicios.MENSAJE_GENERICO
    assert "especialidad" not in error.value.mensaje.lower() and Turno.objects.count() == 1
    assert Franja.objects.get(pk=otra.pk).estado == EstadoFranja.LIBRE
    assert EventoAuditoria.objects.filter(accion="RESERVA_RECHAZADA", detalle__codigo="YA_TIENE_CITA_ESPECIALIDAD").exists()  # queda auditado


def test_T16_tope_de_3_citas_activas_en_total(oferta, ciudadano):
    usuario = ciudadano()
    for esp, hora in ((oferta["esp1"], 8), (oferta["esp2"], 10), (oferta["esp3"], 12)):
        _reservar(usuario, franja_futura(oferta["est1"], esp, hora=hora))
    from apps.turnos.modelos import Especialidad

    cuarta = Especialidad.objects.create(codigo="CARD", nombre="Cardiología")
    with pytest.raises(servicios.ReglaRechazada) as error:
        _reservar(usuario, franja_futura(oferta["est1"], cuarta, hora=15))
    assert error.value.codigo == "TOPE_DE_CITAS"


def test_T16_sin_solape_de_horario_para_la_misma_persona(oferta, ciudadano):
    usuario = ciudadano()
    _reservar(usuario, franja_futura(oferta["est1"], oferta["esp1"], hora=9, minuto=0))
    with pytest.raises(servicios.ReglaRechazada) as error:
        _reservar(usuario, franja_futura(oferta["est2"], oferta["esp2"], hora=9, minuto=15))  # otra especialidad pero a la misma hora
    assert error.value.codigo == "SOLAPE_DE_HORARIO"


def test_hasta_30_dias_de_anticipacion(oferta, ciudadano):
    usuario = ciudadano()
    with pytest.raises(servicios.ReglaRechazada) as error:
        _reservar(usuario, franja_futura(oferta["est1"], oferta["esp1"], dias=31))
    assert error.value.codigo == "FUERA_DE_VENTANA" and "30 días" in error.value.mensaje
    _reservar(usuario, franja_futura(oferta["est1"], oferta["esp1"], dias=29))


def test_una_franja_pasada_o_inexistente_no_se_reserva(oferta, ciudadano):
    usuario = ciudadano()
    pasada = franja_futura(oferta["est1"], oferta["esp1"], dias=-1)
    for franja_id in (pasada.id, uuid.uuid4(), "no-es-un-uuid"):
        with pytest.raises(servicios.ReglaRechazada) as error:
            servicios.reservar(solicitante=usuario, franja_id=franja_id, motivo="", clave_idempotencia=_clave())
        assert error.value.codigo == "FRANJA_NO_DISPONIBLE"


def test_RT04_una_franja_ocupada_no_se_vende_dos_veces(oferta, ciudadano):
    f = franja_futura(oferta["est1"], oferta["esp1"])
    _reservar(ciudadano(), f)
    with pytest.raises(servicios.ReglaRechazada) as error:
        _reservar(ciudadano(), f)
    assert error.value.codigo == "FRANJA_NO_DISPONIBLE"
    assert Turno.objects.filter(franja=f).count() == 1


def test_CA13_el_reintento_con_la_misma_clave_devuelve_el_mismo_turno(oferta, ciudadano):
    usuario = ciudadano()
    f = franja_futura(oferta["est1"], oferta["esp1"])
    clave = _clave()
    primero, creado1 = _reservar(usuario, f, clave=clave)
    segundo, creado2 = _reservar(usuario, f, clave=clave)
    assert creado1 and not creado2 and primero.id == segundo.id
    assert Turno.objects.count() == 1 and SolicitudReserva.objects.count() == 1
    assert EventoAuditoria.objects.filter(accion="RESERVA_REPETIDA").count() == 1


def test_la_misma_clave_con_otra_franja_es_un_conflicto_no_una_segunda_reserva(oferta, ciudadano):
    usuario = ciudadano()
    clave = _clave()
    _reservar(usuario, franja_futura(oferta["est1"], oferta["esp1"], hora=9), clave=clave)
    with pytest.raises(servicios.ConflictoIdempotencia):
        _reservar(usuario, franja_futura(oferta["est1"], oferta["esp2"], hora=10), clave=clave)
    assert Turno.objects.count() == 1


@pytest.mark.parametrize("clave", ["", "corta", "con espacios no vale ya", "x" * 65, "ñ" * 10])
def test_la_clave_de_idempotencia_es_obligatoria_y_con_formato(oferta, ciudadano, clave):
    with pytest.raises(servicios.ReglaRechazada) as error:
        _reservar(ciudadano(), franja_futura(oferta["est1"], oferta["esp1"]), clave=clave)
    assert error.value.codigo == "CLAVE_INVALIDA"


def test_RT10_afiliacion_no_vigente_se_rechaza(oferta, ciudadano):
    usuario = ciudadano(vigente=False)
    with pytest.raises(servicios.ReglaRechazada) as error:
        _reservar(usuario, franja_futura(oferta["est1"], oferta["esp1"]))
    assert error.value.codigo == "SIN_AFILIACION_VIGENTE" and Turno.objects.count() == 0


def test_CA15_si_afiliacion_cae_el_turno_queda_pendiente_y_nunca_se_afirma_cobertura(oferta, ciudadano, correo_enviado):
    usuario = ciudadano()
    integraciones.afiliacion_memoria().caido = True
    turno, creado = _reservar(usuario, franja_futura(oferta["est1"], oferta["esp1"]))
    assert creado and turno.estado == "PENDIENTE"
    assert integraciones.agenda_memoria().llamadas == 0  # ni siquiera se pidió confirmar a la agenda
    assert correo_enviado == []  # no hay aviso de "reserva creada" sin confirmación
    assert EventoAuditoria.objects.filter(accion="AFILIACION_NO_DISPONIBLE").exists()


def test_la_conciliacion_confirma_los_pendientes_cuando_el_tercero_se_recupera(oferta, ciudadano):
    usuario = ciudadano()
    integraciones.afiliacion_memoria().caido = True
    turno, _ = _reservar(usuario, franja_futura(oferta["est1"], oferta["esp1"]))
    integraciones.afiliacion_memoria().caido = False
    reloj.avanzar(timedelta(seconds=10))
    assert servicios.conciliar_pendientes()["reservados"] == 1
    assert Turno.objects.get(pk=turno.id).estado == "RESERVADO"


def test_agenda_caida_deja_pendiente_y_la_conciliacion_lo_resuelve(oferta, ciudadano):
    usuario = ciudadano()
    integraciones.agenda_memoria().caida = True
    turno, _ = _reservar(usuario, franja_futura(oferta["est1"], oferta["esp1"]))
    assert turno.estado == "PENDIENTE" and EventoAuditoria.objects.filter(accion="AGENDA_NO_DISPONIBLE").exists()
    reloj.avanzar(timedelta(seconds=10))
    assert servicios.conciliar_pendientes()["siguen_pendientes"] == 1  # sigue caída
    integraciones.agenda_memoria().caida = False
    reloj.avanzar(timedelta(seconds=10))
    assert servicios.conciliar_pendientes()["reservados"] == 1
    assert Turno.objects.get(pk=turno.id).estado == "RESERVADO"


def test_si_la_agenda_rechaza_el_turno_se_cancela_y_el_cupo_se_libera(oferta, ciudadano):
    f = franja_futura(oferta["est1"], oferta["esp1"])
    integraciones.agenda_memoria().rechazar.add(str(f.id))
    turno, _ = _reservar(ciudadano(), f)
    assert turno.estado == "CANCELADO_INSTITUCION"
    assert Franja.objects.get(pk=f.pk).estado == EstadoFranja.LIBRE


def test_un_pendiente_sin_confirmacion_de_agenda_se_cancela_pasado_su_plazo(oferta, ciudadano):
    integraciones.agenda_memoria().caida = True
    usuario = ciudadano()
    f = franja_futura(oferta["est1"], oferta["esp1"])
    turno, _ = _reservar(usuario, f)
    reloj.avanzar(timedelta(hours=3))
    assert servicios.expirar_vencidos()["pendientes_cancelados"] == 1
    assert Turno.objects.get(pk=turno.id).estado == "CANCELADO_INSTITUCION" and Franja.objects.get(pk=f.pk).estado == EstadoFranja.LIBRE


def test_RT09_diez_reservas_por_hora_y_por_cuenta(oferta, ciudadano):
    usuario = ciudadano()
    for i in range(10):
        with pytest.raises(servicios.ReglaRechazada):
            servicios.reservar(solicitante=usuario, franja_id=uuid.uuid4(), motivo="", clave_idempotencia=_clave())
    from apps.cuentas.servicios import LimiteExcedido

    with pytest.raises(LimiteExcedido):
        servicios.reservar(solicitante=usuario, franja_id=uuid.uuid4(), motivo="", clave_idempotencia=_clave())


def test_el_personal_no_reserva_desde_el_portal(oferta, funcionario):
    recepcion = funcionario("RECEPCION", oferta["est1"])
    with pytest.raises(servicios.AccesoDenegado):
        _reservar(recepcion, franja_futura(oferta["est1"], oferta["esp1"]))


def test_RT11_los_cupos_prioritarios_solo_para_grupos_prioritarios(oferta, ciudadano):
    f = franja_futura(oferta["est1"], oferta["esp1"], prioritaria=True)
    with pytest.raises(servicios.ReglaRechazada) as error:
        _reservar(ciudadano(), f)
    assert error.value.codigo == "FRANJA_NO_DISPONIBLE"  # no revela que existe un cupo prioritario
    turno, _ = _reservar(ciudadano(atencion_prioritaria=True), f)
    assert turno.estado == "RESERVADO"


# --- Delegación y representante (CA-01, RT-08) ---------------------------------------------------------------------------------------------


def _delegar(titular, representante):
    return delegaciones.crear_delegacion(peticion(titular), representante.cedula)


def test_CA01_sin_delegacion_no_se_reserva_a_nombre_de_otra_persona(oferta, ciudadano):
    titular, intruso = ciudadano(), ciudadano("REPRESENTANTE")
    f = franja_futura(oferta["est1"], oferta["esp1"])
    with pytest.raises(servicios.AccesoDenegado):
        _reservar(intruso, f, afiliado_id=titular.id)
    assert Turno.objects.count() == 0 and Franja.objects.get(pk=f.pk).estado == EstadoFranja.LIBRE
    evento = EventoAuditoria.objects.get(accion="RESERVA_RECHAZADA")
    assert evento.detalle["codigo"] == "SIN_DELEGACION" and evento.actor_id == intruso.id and evento.detalle["afiliado_id"] == str(titular.id)


def test_con_delegacion_vigente_el_representante_reserva_y_los_topes_son_del_titular(oferta, ciudadano, correo_enviado):
    titular, rep = ciudadano(), ciudadano("REPRESENTANTE")
    _delegar(titular, rep)
    correo_enviado.clear()
    turno, _ = _reservar(rep, franja_futura(oferta["est1"], oferta["esp1"]), afiliado_id=titular.id)
    assert turno.afiliado_id == titular.id and Turno.objects.get().solicitante_id == rep.id
    # RT-08: el tope de 1 por especialidad se evalúa sobre el TITULAR, no sobre quien opera.
    with pytest.raises(servicios.ReglaRechazada) as error:
        _reservar(rep, franja_futura(oferta["est1"], oferta["esp1"], hora=11), afiliado_id=titular.id)
    assert error.value.codigo == "YA_TIENE_CITA_ESPECIALIDAD"
    # El representante todavía puede reservar para SÍ MISMO en esa especialidad (sus topes son otros).
    _reservar(rep, franja_futura(oferta["est1"], oferta["esp1"], hora=12))
    # El titular recibe el aviso neutral por la acción del representante.
    avisos = [m for m in correo_enviado if m.plantilla == "accion_representante"]
    assert avisos and avisos[0].destino == titular.correo and avisos[0].cuerpo == "Se gestionó una cita con su delegación."


def test_la_auditoria_de_una_accion_delegada_lleva_ambos_identificadores(oferta, ciudadano):
    titular, rep = ciudadano(), ciudadano("REPRESENTANTE")
    _delegar(titular, rep)
    _reservar(rep, franja_futura(oferta["est1"], oferta["esp1"]), afiliado_id=titular.id)
    evento = EventoAuditoria.objects.filter(accion="RESERVA_CREADA").latest("secuencia")
    assert evento.actor_id == rep.id and evento.detalle["afiliado_id"] == str(titular.id) and evento.detalle["representante_id"] == str(rep.id)


def test_la_revocacion_surte_efecto_en_la_siguiente_solicitud(oferta, ciudadano):
    titular, rep = ciudadano(), ciudadano("REPRESENTANTE")
    d = _delegar(titular, rep)
    delegaciones.revocar_delegacion(titular, d.id)
    with pytest.raises(servicios.AccesoDenegado):
        _reservar(rep, franja_futura(oferta["est1"], oferta["esp1"]), afiliado_id=titular.id)


def test_una_delegacion_vencida_no_sirve(oferta, ciudadano):
    titular, rep = ciudadano(), ciudadano("REPRESENTANTE")
    _delegar(titular, rep)
    reloj.avanzar(timedelta(days=366))
    with pytest.raises(servicios.AccesoDenegado):
        _reservar(rep, franja_futura(oferta["est1"], oferta["esp1"], dias=3 + 0), afiliado_id=titular.id)

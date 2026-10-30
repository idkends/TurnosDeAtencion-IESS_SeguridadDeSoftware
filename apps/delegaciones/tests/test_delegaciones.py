"""Delegaciones (§4.4, RT-08, R04, T02): alcance solo agenda, 12 meses, hasta 5, revocación inmediata y aviso al titular."""
from datetime import timedelta

import pytest

from apps.auditoria.modelos import EventoAuditoria
from apps.cuentas.servicios import ReautenticacionRequerida
from apps.delegaciones import servicios
from apps.delegaciones.modelos import Delegacion
from config import reloj
from tests.ayudas import peticion

pytestmark = pytest.mark.django_db


def _crear(titular, representante, **kw):
    return servicios.crear_delegacion(peticion(titular, **kw), representante.cedula)


def test_el_titular_crea_una_delegacion_de_agenda_por_12_meses(ciudadano, correo_enviado):
    titular, rep = ciudadano(), ciudadano("REPRESENTANTE")
    d = _crear(titular, rep)
    assert d.vigente and d.tipo == "TITULAR" and d.representante_nombre == rep.nombres + " " + rep.apellidos
    assert 364 <= (d.vence - d.inicio).days <= 366
    assert Delegacion.objects.get().alcance == "AGENDA"  # nunca datos clínicos: el único alcance posible es la agenda
    assert [m.plantilla for m in correo_enviado] == ["delegacion_creada"] and correo_enviado[0].destino == titular.correo


def test_el_alcance_solo_puede_ser_agenda_por_restriccion_de_la_base(ciudadano):
    from django.db import IntegrityError, transaction

    titular, rep = ciudadano(), ciudadano("REPRESENTANTE")
    with pytest.raises(IntegrityError), transaction.atomic():
        Delegacion.objects.create(titular=titular, representante=rep, alcance="CLINICO", inicio=reloj.ahora(), vence=reloj.ahora() + timedelta(days=30))


def test_T02_crear_una_delegacion_exige_segundo_factor_reciente(ciudadano):
    titular, rep = ciudadano(), ciudadano("REPRESENTANTE")
    with pytest.raises(ReautenticacionRequerida):
        servicios.crear_delegacion(peticion(titular, mfa=False), rep.cedula)  # sin TOTP configurado: debe activarlo
    peticion_vieja = peticion(titular)
    peticion_vieja.session = type(peticion_vieja.session)(get={"ultima_actividad": reloj.ahora().timestamp(), "mfa_en": reloj.ahora().timestamp() - 3600}.get)
    with pytest.raises(ReautenticacionRequerida) as error:
        servicios.crear_delegacion(peticion_vieja, rep.cedula)
    assert error.value.motivo == "MFA_NO_RECIENTE" and not Delegacion.objects.exists()


def test_el_representante_debe_existir_estar_activo_y_ser_representante_con_mensaje_unico(ciudadano, crear_usuario):
    titular = ciudadano()
    otro_afiliado = ciudadano("AFILIADO")  # un afiliado común no es representante
    suspendido = crear_usuario("REPRESENTANTE", estado="SUSPENDIDA")
    for cedula in (otro_afiliado.cedula, suspendido.cedula, "1750999990", "no-es-cedula", titular.cedula):
        with pytest.raises(servicios.DelegacionRechazada) as error:
            servicios.crear_delegacion(peticion(titular), cedula)
        assert str(error.value) == "REPRESENTANTE_NO_VALIDO"  # el mismo error para todos: no delata qué cédulas existen
    assert not Delegacion.objects.exists()
    assert EventoAuditoria.objects.filter(accion="DELEGACION_RECHAZADA").count() == 5


def test_nadie_se_delega_a_si_mismo(ciudadano):
    rep = ciudadano("REPRESENTANTE")
    with pytest.raises(servicios.DelegacionRechazada):
        servicios.crear_delegacion(peticion(rep), rep.cedula)


def test_la_previsualizacion_muestra_el_nombre_para_confirmar(ciudadano):
    titular, rep = ciudadano(), ciudadano("REPRESENTANTE", nombres="Marta", apellidos="Lozano")
    persona = servicios.previsualizar_representante(peticion(titular), rep.cedula)
    assert persona.nombre_completo == "Marta Lozano"


def test_RT08_un_representante_gestiona_hasta_5_personas(ciudadano):
    rep = ciudadano("REPRESENTANTE")
    for _ in range(5):
        _crear(ciudadano(), rep)
    sexto = ciudadano()
    with pytest.raises(servicios.DelegacionRechazada) as error:
        _crear(sexto, rep)
    assert str(error.value) == "LIMITE_REPRESENTADOS" and Delegacion.objects.filter(representante=rep).count() == 5


def test_el_limite_de_5_cuenta_solo_las_delegaciones_vigentes(ciudadano):
    rep = ciudadano("REPRESENTANTE")
    titulares = [ciudadano() for _ in range(5)]
    ds = [_crear(t, rep) for t in titulares]
    servicios.revocar_delegacion(titulares[0], ds[0].id)
    _crear(ciudadano(), rep)  # ahora hay lugar


def test_crear_dos_veces_la_misma_delegacion_es_idempotente(ciudadano):
    titular, rep = ciudadano(), ciudadano("REPRESENTANTE")
    a, b = _crear(titular, rep), _crear(titular, rep)
    assert a.id == b.id and Delegacion.objects.count() == 1


def test_revocar_es_inmediato_idempotente_y_avisa_al_titular(ciudadano, correo_enviado):
    titular, rep = ciudadano(), ciudadano("REPRESENTANTE")
    d = _crear(titular, rep)
    assert servicios.delegacion_vigente(titular.id, rep.id)
    correo_enviado.clear()
    servicios.revocar_delegacion(rep, d.id)  # también el propio representante puede renunciar
    assert not servicios.delegacion_vigente(titular.id, rep.id)  # surte efecto de inmediato, sin esperar a que caduque la sesión
    servicios.revocar_delegacion(titular, d.id)  # repetir no falla ni vuelve a avisar
    assert [m.plantilla for m in correo_enviado] == ["delegacion_revocada"] and correo_enviado[0].destino == titular.correo
    assert EventoAuditoria.objects.filter(accion="DELEGACION_REVOCADA").count() == 1


def test_solo_el_titular_o_el_representante_revocan_el_resto_recibe_404(ciudadano, funcionario, oferta):
    titular, rep, ajeno = ciudadano(), ciudadano("REPRESENTANTE"), ciudadano()
    d = _crear(titular, rep)
    for intruso in (ajeno, funcionario("RECEPCION", oferta["est1"]), funcionario("SOPORTE")):
        with pytest.raises(servicios.NoEncontrado):
            servicios.revocar_delegacion(intruso, d.id)
    with pytest.raises(servicios.NoEncontrado):
        servicios.revocar_delegacion(titular, "00000000-0000-0000-0000-000000000000")
    with pytest.raises(servicios.NoEncontrado):
        servicios.revocar_delegacion(titular, "basura")
    assert servicios.delegacion_vigente(titular.id, rep.id)


def test_la_auditoria_lleva_ambos_identificadores(ciudadano):
    titular, rep = ciudadano(), ciudadano("REPRESENTANTE")
    _crear(titular, rep)
    e = EventoAuditoria.objects.get(accion="DELEGACION_CREADA")
    assert e.actor_id == titular.id and e.detalle["titular_id"] == str(titular.id) and e.detalle["representante_id"] == str(rep.id)


def test_listar_y_representados_solo_incluyen_lo_vigente(ciudadano):
    titular, rep = ciudadano(), ciudadano("REPRESENTANTE")
    d = _crear(titular, rep)
    otorgadas, _ = servicios.listar_delegaciones(titular)
    _, recibidas = servicios.listar_delegaciones(rep)
    assert [x.id for x in otorgadas] == [d.id] == [x.id for x in recibidas] and otorgadas[0].puede_revocar
    assert [p.id for p in servicios.representados_de(rep)] == [titular.id]
    servicios.revocar_delegacion(titular, d.id)
    assert servicios.representados_de(rep) == [] and servicios.listar_delegaciones(titular)[0] == []


def test_una_delegacion_vencida_deja_de_valer_y_no_cuenta_para_el_tope(ciudadano):
    titular, rep = ciudadano(), ciudadano("REPRESENTANTE")
    _crear(titular, rep)
    reloj.avanzar(timedelta(days=366))
    assert not servicios.delegacion_vigente(titular.id, rep.id) and servicios.representados_de(rep) == []


def test_el_tutor_legal_verificado_en_ventanilla_crea_la_delegacion(ciudadano, funcionario, oferta, correo_enviado):
    menor, tutor = ciudadano(), ciudadano("REPRESENTANTE")
    recepcion = funcionario("RECEPCION", oferta["est1"])
    d = servicios.crear_delegacion_tutor(recepcion, menor.cedula, tutor.cedula)
    assert d.tipo == "TUTOR_LEGAL" and Delegacion.objects.get().verificada_por_id == recepcion.id
    assert EventoAuditoria.objects.get(accion="DELEGACION_CREADA").detalle["verificada_por"] == str(recepcion.id)


def test_el_tutor_solo_lo_registra_recepcion(ciudadano, funcionario, oferta):
    menor, tutor = ciudadano(), ciudadano("REPRESENTANTE")
    for intruso in (ciudadano(), funcionario("SOPORTE"), funcionario("ADMIN_TI")):
        with pytest.raises(servicios.AccesoDenegado):
            servicios.crear_delegacion_tutor(intruso, menor.cedula, tutor.cedula)


def test_el_personal_no_crea_delegaciones_como_titular(funcionario, ciudadano, oferta):
    rep = ciudadano("REPRESENTANTE")
    with pytest.raises(servicios.AccesoDenegado):
        servicios.crear_delegacion(peticion(funcionario("RECEPCION", oferta["est1"])), rep.cedula)

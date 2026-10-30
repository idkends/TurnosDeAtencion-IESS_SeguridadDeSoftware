"""T04, R06, API5: matriz de permisos por rol contra TODOS los endpoints (§4.3). Denegar por defecto: lo que no se concede, 403."""
import uuid
from datetime import timedelta

import pytest
from django.test import Client

from apps.auditoria.modelos import EventoAuditoria
from apps.turnos.modelos import Franja, Turno
from config import reloj
from tests.ayudas import HTTPS, franja_futura, iniciar_sesion_con_mfa, registrar_afiliacion

pytestmark = pytest.mark.django_db

ROLES = ["AFILIADO", "REPRESENTANTE", "RECEPCION", "PROFESIONAL", "SOPORTE", "ADMIN_OFERTA", "ADMIN_TI", "AUDITOR_DPD"]
CIUDADANOS = {"AFILIADO", "REPRESENTANTE"}


@pytest.fixture
def escenario(oferta, ciudadano):
    """Un afiliado con un turno CONFIRMADO cuya hora ya pasó, en el establecimiento 1."""
    afiliado = ciudadano()
    franja = franja_futura(oferta["est1"], oferta["esp1"], dias=-1)
    turno = Turno.objects.create(afiliado=afiliado, solicitante=afiliado, especialidad=oferta["esp1"], franja=franja, estado="CONFIRMADO",
                                 inicio=franja.inicio, fin=franja.fin, confirmado_en=reloj.ahora())
    Franja.objects.filter(pk=franja.pk).update(estado="OCUPADA")
    libre = franja_futura(oferta["est1"], oferta["esp2"], dias=5)
    return {"afiliado": afiliado, "turno": turno, "libre": libre, "oferta": oferta}


def _usuario(rol, escenario, crear_usuario):
    est = None if rol in CIUDADANOS or rol in ("SOPORTE", "ADMIN_TI", "AUDITOR_DPD") else escenario["oferta"]["est1"]
    usuario = crear_usuario(rol, establecimiento=est)
    registrar_afiliacion(usuario)  # los ciudadanos tienen afiliación vigente (el personal no la usa)
    return usuario


def _cliente(usuario):
    c = Client()
    iniciar_sesion_con_mfa(c, usuario)
    return c


# endpoint -> (función que llama, roles con éxito, códigos de éxito)
def _e01(c, e): return c.get("/disponibilidad", **HTTPS)
def _e06(c, e): return c.get("/turnos", **HTTPS)
def _e08(c, e): return c.post("/turnos", {"franja": str(e["libre"].id), "clave_idempotencia": uuid.uuid4().hex}, **HTTPS)
def _e13(c, e): return c.post("/delegaciones", {"cedula": e["afiliado"].cedula, "paso": "verificar"}, **HTTPS)
def _e15(c, e): return c.get("/mis-datos/solicitudes", **HTTPS)
def _e16(c, e): return c.post(f"/interno/turnos/{e['turno'].id}/asistencia", {"resultado": "NO_ASISTIO"}, **HTTPS)
def _e17(c, e): return c.get("/interno/agenda", **HTTPS)
def _e18(c, e): return c.get(f"/interno/afiliados/{e['afiliado'].cedula}", **HTTPS)
def _e19(c, e): return c.get("/auditoria", **HTTPS)
def _e21(c, e): return c.get("/salud", **HTTPS)


MATRIZ = {
    "E01": (_e01, set(ROLES), {200}),
    "E06": (_e06, CIUDADANOS, {200}),
    "E08": (_e08, CIUDADANOS, {302}),
    "E13": (_e13, CIUDADANOS, {409}),  # 409: la cédula del afiliado no es de un representante (mensaje único); lo importante es NO 403
    "E15": (_e15, CIUDADANOS, {200}),
    "E16": (_e16, {"RECEPCION"}, {302}),
    "E17": (_e17, {"RECEPCION", "PROFESIONAL", "ADMIN_OFERTA"}, {200}),
    "E18": (_e18, {"RECEPCION", "SOPORTE"}, {200}),
    "E19": (_e19, {"AUDITOR_DPD"}, {200}),
    "E21": (_e21, set(ROLES), {200}),
}


@pytest.mark.parametrize("endpoint", sorted(MATRIZ))
@pytest.mark.parametrize("rol", ROLES)
def test_T04_matriz_de_permisos(endpoint, rol, escenario, crear_usuario):
    llamar, permitidos, exitos = MATRIZ[endpoint]
    cliente = _cliente(_usuario(rol, escenario, crear_usuario))
    r = llamar(cliente, escenario)
    if rol in permitidos:
        assert r.status_code in exitos, (endpoint, rol, r.status_code)
    else:
        assert r.status_code == 403, (endpoint, rol, r.status_code)  # denegado por defecto


@pytest.mark.parametrize("endpoint", [e for e in MATRIZ if e not in ("E01", "E21")])
def test_los_endpoints_protegidos_redirigen_al_ingreso_sin_sesion(endpoint, escenario):
    r = MATRIZ[endpoint][0](Client(), escenario)
    assert r.status_code == 302 and r["Location"].startswith("/sesion"), (endpoint, r.status_code)


def test_E01_y_E21_son_publicos(escenario):
    assert _e01(Client(), escenario).status_code == 200 and _e21(Client(), escenario).status_code == 200


def test_el_afiliado_nunca_llega_a_las_rutas_internas(escenario):
    c = _cliente(escenario["afiliado"])
    for ruta in ("/interno/agenda", f"/interno/afiliados/{escenario['afiliado'].cedula}", "/auditoria"):
        assert c.get(ruta, **HTTPS).status_code == 403, ruta
    assert c.post(f"/interno/turnos/{escenario['turno'].id}/asistencia", {"resultado": "ATENDIDO"}, **HTTPS).status_code == 403
    assert Turno.objects.get(pk=escenario["turno"].id).estado == "CONFIRMADO"  # y no pudo marcarse a sí mismo como "asistió"


# --- Unidad: lo que ve cada funcionario depende de SU unidad ------------------------------------------------------------------------------------


def test_CA09_recepcion_de_otra_unidad_no_ve_la_agenda_ni_los_datos_de_la_unidad_ajena(escenario, crear_usuario):
    oferta = escenario["oferta"]
    ajena = crear_usuario("RECEPCION", establecimiento=oferta["est2"])
    c = _cliente(ajena)
    assert "Agenda de la unidad" in c.get("/interno/agenda", **HTTPS).content.decode()
    assert escenario["afiliado"].nombres not in c.get("/interno/agenda", **HTTPS).content.decode()  # su agenda no tiene ese turno
    r = c.get(f"/interno/afiliados/{escenario['afiliado'].cedula}", **HTTPS)
    assert r.status_code == 403
    assert EventoAuditoria.objects.filter(accion="ACCESO_DENEGADO", actor_id=ajena.id, resultado="DENEGADO").exists()  # denegación auditada
    assert c.post(f"/interno/turnos/{escenario['turno'].id}/asistencia", {"resultado": "NO_ASISTIO"}, **HTTPS).status_code == 403
    assert Turno.objects.get(pk=escenario["turno"].id).estado == "CONFIRMADO"


def test_el_funcionario_sin_unidad_asignada_no_ve_agendas(crear_usuario, escenario):
    sin_unidad = crear_usuario("RECEPCION", establecimiento=None)
    assert _cliente(sin_unidad).get("/interno/agenda", **HTTPS).status_code == 403


def test_E17_la_agenda_muestra_cedula_enmascarada_sin_datos_de_contacto_ni_clinicos(escenario, crear_usuario):
    recepcion = crear_usuario("RECEPCION", establecimiento=escenario["oferta"]["est1"])
    af = escenario["afiliado"]
    cuerpo = _cliente(recepcion).get(f"/interno/agenda?fecha={zona_fecha(escenario['turno'])}", **HTTPS).content.decode()
    assert af.nombres in cuerpo and af.cedula not in cuerpo and f"{af.cedula[:3]}*****{af.cedula[-2:]}" in cuerpo
    assert af.correo not in cuerpo and af.telefono not in cuerpo
    assert "diagn" not in cuerpo.lower()


def zona_fecha(turno):
    from django.utils import timezone as zona

    return zona.localdate(turno.inicio).isoformat()


def test_E17_paginacion_y_parametros_en_lista_blanca(escenario, crear_usuario):
    c = _cliente(crear_usuario("RECEPCION", establecimiento=escenario["oferta"]["est1"]))
    assert c.get("/interno/agenda?tamano=1000", **HTTPS).status_code == 400
    assert c.get("/interno/agenda?formato=csv", **HTTPS).status_code == 400  # no hay exportación: ni siquiera existe el parámetro
    assert c.get("/interno/agenda?fecha=ayer", **HTTPS).status_code == 400


# --- E18: contacto enmascarado y justificación ------------------------------------------------------------------------------------------------------


def test_E18_recepcion_ve_datos_enmascarados_y_completos_solo_con_justificacion(escenario, crear_usuario):
    af = escenario["afiliado"]
    c = _cliente(crear_usuario("RECEPCION", establecimiento=escenario["oferta"]["est1"]))
    sin_urls = lambda html: html.replace(f"/interno/afiliados/{af.cedula}", "")  # la cédula de la URL la escribió la propia persona
    enmascarado = sin_urls(c.get(f"/interno/afiliados/{af.cedula}", **HTTPS).content.decode())
    assert af.correo not in enmascarado and af.telefono not in enmascarado and af.cedula not in enmascarado
    assert f"{af.correo[0]}***@" in enmascarado and "Datos enmascarados" in enmascarado
    corta = sin_urls(c.get(f"/interno/afiliados/{af.cedula}?justificacion=corta", **HTTPS).content.decode())
    assert af.correo not in corta  # una justificación demasiado breve no desbloquea
    completo = c.get(f"/interno/afiliados/{af.cedula}?justificacion=Reprogramacion solicitada por llamada", **HTTPS).content.decode()
    assert af.correo in completo and af.telefono in completo and "con justificación registrada" in completo
    registro = EventoAuditoria.objects.filter(accion="CONSULTA_CONTACTO").latest("secuencia")
    assert registro.detalle["completo"] is True and "llamada" in registro.detalle["justificacion"]  # la justificación queda registrada


def test_E18_soporte_solo_ve_datos_enmascarados_aun_con_justificacion(escenario, crear_usuario):
    af = escenario["afiliado"]
    c = _cliente(crear_usuario("SOPORTE"))
    cuerpo = c.get(f"/interno/afiliados/{af.cedula}?justificacion=Necesito ver todo por favor", **HTTPS).content.decode()
    assert af.correo not in cuerpo and af.telefono not in cuerpo and "Datos enmascarados" in cuerpo


def test_E18_cedula_inexistente_404_y_formato_invalido_ni_existe_la_ruta(escenario, crear_usuario):
    c = _cliente(crear_usuario("SOPORTE"))
    assert c.get("/interno/afiliados/1750999990", **HTTPS).status_code == 404
    assert c.get("/interno/afiliados/abc", **HTTPS).status_code == 404
    assert c.get(f"/interno/afiliados/{escenario['afiliado'].cedula}?otro=1", **HTTPS).status_code == 400


def test_R06_E18_maximo_30_consultas_por_hora_y_por_funcionario(escenario, crear_usuario):
    c = _cliente(crear_usuario("SOPORTE"))
    ruta = f"/interno/afiliados/{escenario['afiliado'].cedula}"
    for _ in range(30):
        assert c.get(ruta, **HTTPS).status_code == 200
    r = c.get(ruta, **HTTPS)
    assert r.status_code == 429 and "Retry-After" in r


# --- E19: auditoría -------------------------------------------------------------------------------------------------------------------------------------


def test_E19_el_auditor_filtra_en_lista_blanca_y_el_acceso_queda_registrado(escenario, crear_usuario):
    c = _cliente(crear_usuario("AUDITOR_DPD"))
    r = c.get("/auditoria?accion=LOGIN_OK&resultado=OK&desde=2020-01-01", **HTTPS)
    cuerpo = r.content.decode()
    assert r.status_code == 200 and "LOGIN_OK" in cuerpo and "Solo lectura" in cuerpo
    for malo in ("/auditoria?accion=' OR 1=1--", "/auditoria?accion=NO_EXISTE_XYZ", "/auditoria?resultado=TAL", "/auditoria?actor=no-uuid", "/auditoria?orden=asc", "/auditoria?desde=hoy"):
        assert c.get(malo, **HTTPS).status_code == 400, malo
    assert EventoAuditoria.objects.filter(accion="ACCESO_AUDITORIA").exists()


def test_E19_la_pagina_de_auditoria_no_muestra_secretos(escenario, crear_usuario):
    from apps.auditoria import servicios as auditoria

    auditoria.registrar(accion="PRUEBA_SECRETOS", detalle={"clave": "Secreta-123", "token": "abc", "codigo_activacion": "12345678"})
    cuerpo = _cliente(crear_usuario("AUDITOR_DPD")).get("/auditoria?pagina=1", **HTTPS).content.decode()
    assert "Secreta-123" not in cuerpo and "12345678" not in cuerpo

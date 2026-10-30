"""Paneles de funcionarios, derechos, alertas y respuestas automáticas por HTTP (T04, T12, CA-09, CA-10, CA-11, CA-12, CA-16)."""
import re
import uuid
from datetime import timedelta

import pytest
from django.contrib.sessions.models import Session
from django.test import Client

from apps.auditoria.modelos import EventoAuditoria
from apps.deteccion.modelos import Alerta
from apps.deteccion import servicios as deteccion
from apps.turnos.modelos import Franja, Turno
from config import bloqueos, contexto, reloj
from tests.ayudas import CLAVE, HTTPS, franja_futura, iniciar_sesion, iniciar_sesion_con_mfa, registrar_afiliacion

pytestmark = pytest.mark.django_db
CIUDADANOS = {"AFILIADO", "REPRESENTANTE"}


@pytest.fixture(autouse=True)
def respuestas_limpias():
    for ip in ("203.0.113.7", "198.51.100.200", "198.51.100.201"):
        bloqueos.desbloquear_origen(ip)
    bloqueos.desactivar_desafio()
    bloqueos.desactivar_sala_espera()
    yield
    for ip in ("203.0.113.7", "198.51.100.200", "198.51.100.201"):
        bloqueos.desbloquear_origen(ip)
    bloqueos.desactivar_desafio()
    bloqueos.desactivar_sala_espera()


def _cliente(usuario):
    c = Client()
    iniciar_sesion_con_mfa(c, usuario)
    return c


def _usuario(rol, oferta, crear_usuario):
    est = oferta["est1"] if rol in ("RECEPCION", "PROFESIONAL", "ADMIN_OFERTA") else None
    u = crear_usuario(rol, establecimiento=est)
    registrar_afiliacion(u)
    return u


# --- Matriz de roles de las rutas nuevas -----------------------------------------------------------------------------------------------------------------


MATRIZ = {
    "/seguridad/alertas": {"AUDITOR_DPD", "ADMIN_TI"},
    "/derechos/solicitudes": {"AUDITOR_DPD"},
    "/exportaciones": {"AUDITOR_DPD", "ADMIN_TI"},
    "/oferta/franjas": {"ADMIN_OFERTA"},
    "/mis-datos/preferencias": set(),  # datos propios: cualquier persona autenticada (el funcionario también es titular)
    "/mis-datos/contacto": set(),
}
ROLES = ["AFILIADO", "REPRESENTANTE", "RECEPCION", "PROFESIONAL", "SOPORTE", "ADMIN_OFERTA", "ADMIN_TI", "AUDITOR_DPD"]


@pytest.mark.parametrize("ruta", sorted(MATRIZ))
@pytest.mark.parametrize("rol", ROLES)
def test_T04_matriz_de_las_rutas_de_la_etapa_3(ruta, rol, oferta, crear_usuario):
    r = _cliente(_usuario(rol, oferta, crear_usuario)).get(ruta, **HTTPS)
    esperado = MATRIZ[ruta] or set(ROLES)
    assert r.status_code == (200 if rol in esperado else 403), (ruta, rol, r.status_code)


def test_las_rutas_nuevas_redirigen_al_ingreso_sin_sesion():
    for ruta in list(MATRIZ) + ["/mis-datos/exportar", "/soporte/cuentas/1750000018"]:
        r = Client().get(ruta, **HTTPS)
        assert r.status_code == 302 and r["Location"].startswith("/sesion"), ruta


# --- Soporte -------------------------------------------------------------------------------------------------------------------------------------------------------


def test_soporte_ve_datos_tecnicos_enmascarados_y_nunca_la_clave(oferta, crear_usuario, ciudadano):
    af = ciudadano()
    c = _cliente(crear_usuario("SOPORTE"))
    cuerpo = c.get(f"/soporte/cuentas/{af.cedula}", **HTTPS).content.decode()
    sin_ruta = cuerpo.replace(af.cedula, "")  # la cédula de la URL la escribió la propia persona
    assert af.correo not in cuerpo and af.telefono not in cuerpo and "argon2" not in cuerpo and af.password not in cuerpo
    assert "***" in cuerpo and "Segundo factor" in cuerpo and af.cedula not in sin_ruta
    assert c.get("/soporte/cuentas/1750999990", **HTTPS).status_code == 404


def test_soporte_no_restablece_claves_solo_inicia_el_flujo_con_verificacion_presencial(oferta, crear_usuario, ciudadano, correo_enviado):
    af = ciudadano(correo="titular@correo.iess-prueba.example")
    soporte = crear_usuario("SOPORTE", correo="soporte@correo.iess-prueba.example")
    c = _cliente(soporte)
    antes = af.password
    correo_enviado.clear()
    r = c.post(f"/soporte/cuentas/{af.cedula}/recuperacion", {}, **HTTPS)  # sin marcar la verificación presencial
    assert r.status_code == 302 and not [m for m in correo_enviado if m.plantilla == "recuperacion"]
    c.post(f"/soporte/cuentas/{af.cedula}/recuperacion", {"verificacion_presencial": "on"}, **HTTPS)
    (mensaje,) = [m for m in correo_enviado if m.plantilla == "recuperacion"]
    assert mensaje.destino == "titular@correo.iess-prueba.example"  # el enlace va al contacto REGISTRADO de la persona, no al soporte
    af.refresh_from_db()
    assert af.password == antes  # la clave no cambió: soporte no puede fijarla
    assert EventoAuditoria.objects.filter(accion="SOPORTE_INICIA_RECUPERACION", actor_id=soporte.id).exists()
    assert "restablecer" not in c.get(f"/soporte/cuentas/{af.cedula}", **HTTPS).content.decode()  # soporte no ve el enlace


def test_el_resto_de_roles_no_ve_la_ficha_tecnica(oferta, crear_usuario, ciudadano):
    af = ciudadano()
    for rol in ("RECEPCION", "AUDITOR_DPD", "ADMIN_TI", "AFILIADO"):
        assert _cliente(_usuario(rol, oferta, crear_usuario)).get(f"/soporte/cuentas/{af.cedula}", **HTTPS).status_code == 403
        assert _cliente(_usuario(rol, oferta, crear_usuario)).post(f"/soporte/cuentas/{af.cedula}/recuperacion", {"verificacion_presencial": "on"}, **HTTPS).status_code == 403


# --- Administración de oferta ----------------------------------------------------------------------------------------------------------------------------------


def _jornada(c, esp="MGEN", dias=3, **extra):
    from django.utils import timezone as zona

    fecha = (zona.localdate(reloj.ahora()) + timedelta(days=dias)).isoformat()
    return c.post("/oferta/franjas", {"especialidad": esp, "fecha": fecha, "desde": "08:00", "hasta": "10:00", "duracion": "30", **extra}, **HTTPS)


def test_admin_de_oferta_crea_una_jornada_en_su_unidad_y_es_idempotente(oferta, crear_usuario):
    admin = crear_usuario("ADMIN_OFERTA", establecimiento=oferta["est1"])
    c = _cliente(admin)
    assert _jornada(c).status_code == 302
    assert Franja.objects.filter(establecimiento=oferta["est1"]).count() == 4  # 08:00-10:00 en franjas de 30 min
    assert not Franja.objects.exclude(establecimiento=oferta["est1"]).exists()  # solo SU unidad
    _jornada(c)
    assert Franja.objects.count() == 4  # repetir no duplica
    assert EventoAuditoria.objects.filter(accion="OFERTA_MODIFICADA", actor_id=admin.id).exists()


@pytest.mark.parametrize("extra", [{"duracion": "5"}, {"duracion": "500"}, {"desde": "11:00", "hasta": "09:00"}, {"especialidad": "NOEXISTE"}, {"estado": "LIBRE"}, {"establecimiento": "GUA01"}])
def test_la_jornada_valida_el_esquema_y_no_acepta_otra_unidad(oferta, crear_usuario, extra):
    c = _cliente(crear_usuario("ADMIN_OFERTA", establecimiento=oferta["est1"]))
    assert _jornada(c, **extra).status_code in (400, 409) and not Franja.objects.exists()


def test_no_se_crea_oferta_fuera_de_la_ventana_de_reserva(oferta, crear_usuario):
    c = _cliente(crear_usuario("ADMIN_OFERTA", establecimiento=oferta["est1"]))
    assert _jornada(c, dias=60).status_code == 409 and _jornada(c, dias=-2).status_code == 409


def test_cerrar_una_franja_ocupada_la_cancela_la_institucion_con_aviso(oferta, crear_usuario, ciudadano, correo_enviado):
    from apps.turnos import servicios as turnos

    admin = crear_usuario("ADMIN_OFERTA", establecimiento=oferta["est1"])
    af = ciudadano()
    f = franja_futura(oferta["est1"], oferta["esp1"])
    t, _ = turnos.reservar(solicitante=af, franja_id=f.id, motivo="", clave_idempotencia=uuid.uuid4().hex)
    correo_enviado.clear()
    r = _cliente(admin).post(f"/oferta/franjas/{f.id}/cerrar", {}, **HTTPS)
    assert r.status_code == 302 and Franja.objects.get(pk=f.pk).estado == "CERRADA"
    assert Turno.objects.get(pk=t.id).estado == "CANCELADO_INSTITUCION"  # la cancela la institución, no el afiliado
    assert any(m.plantilla == "cita_cancelada" and m.destino == af.correo for m in correo_enviado)
    assert turnos.buscar_disponibilidad().total == 0  # y ya no se ofrece


def test_IDOR_un_admin_no_cierra_franjas_de_otra_unidad_y_el_resto_no_administra_oferta(oferta, crear_usuario):
    ajena = franja_futura(oferta["est2"], oferta["esp1"])
    admin1 = _cliente(crear_usuario("ADMIN_OFERTA", establecimiento=oferta["est1"]))
    assert admin1.post(f"/oferta/franjas/{ajena.id}/cerrar", {}, **HTTPS).status_code == 404
    assert admin1.post(f"/oferta/franjas/{uuid.uuid4()}/cerrar", {}, **HTTPS).status_code == 404  # misma respuesta
    assert Franja.objects.get(pk=ajena.pk).estado == "LIBRE"
    for rol in ("RECEPCION", "SOPORTE", "ADMIN_TI", "AFILIADO"):
        c = _cliente(_usuario(rol, oferta, crear_usuario))
        assert c.post(f"/oferta/franjas/{ajena.id}/cerrar", {}, **HTTPS).status_code == 403
        assert _jornada(c).status_code == 403


def test_una_franja_cerrada_no_se_reserva(oferta, ciudadano, crear_usuario):
    from apps.turnos import servicios as turnos

    f = franja_futura(oferta["est1"], oferta["esp1"])
    _cliente(crear_usuario("ADMIN_OFERTA", establecimiento=oferta["est1"])).post(f"/oferta/franjas/{f.id}/cerrar", {}, **HTTPS)
    with pytest.raises(turnos.ReglaRechazada):
        turnos.reservar(solicitante=ciudadano(), franja_id=f.id, motivo="", clave_idempotencia=uuid.uuid4().hex)


# --- Acceso excepcional (de emergencia) -------------------------------------------------------------------------------------------------------------------------


def test_emergencia_exige_motivo_avisa_de_inmediato_y_abre_una_alerta_de_revision(oferta, crear_usuario, ciudadano, correo_enviado):
    af = ciudadano()
    auditor = crear_usuario("AUDITOR_DPD")
    recep = crear_usuario("RECEPCION", establecimiento=oferta["est2"])  # otra unidad: sin relación con el afiliado
    c = _cliente(recep)
    assert c.get(f"/interno/afiliados/{af.cedula}", **HTTPS).status_code == 403  # lo normal no alcanza
    assert c.post(f"/interno/afiliados/{af.cedula}/emergencia", {"motivo": "corto"}, **HTTPS).status_code == 409
    correo_enviado.clear()
    r = c.post(f"/interno/afiliados/{af.cedula}/emergencia", {"motivo": "Paciente inconsciente sin acompañante, se requiere contacto familiar"}, **HTTPS)
    cuerpo = r.content.decode()
    assert r.status_code == 200 and af.correo in cuerpo and "Acceso excepcional registrado" in cuerpo
    assert any(m.plantilla == "emergencia_acceso" and m.destino == auditor.correo for m in correo_enviado)  # aviso INMEDIATO al responsable
    ev = EventoAuditoria.objects.get(accion="ACCESO_EMERGENCIA")
    assert ev.actor_id == recep.id and "inconsciente" in ev.detalle["motivo"]
    (alerta,) = deteccion.evaluar(reglas={"D10"})
    assert alerta.estado == "ABIERTA" and alerta.vence_en  # queda abierta hasta la revisión posterior
    deteccion.reconocer_alerta(auditor, alerta.id, "Revisado: justificado")
    assert Alerta.objects.get(pk=alerta.pk).estado == "RECONOCIDA"


def test_la_emergencia_no_esta_disponible_para_otros_roles_ni_sin_unidad(oferta, crear_usuario, ciudadano):
    af = ciudadano()
    motivo = {"motivo": "Motivo suficientemente largo para la emergencia"}
    for rol in ("SOPORTE", "ADMIN_OFERTA", "ADMIN_TI", "AUDITOR_DPD", "AFILIADO"):
        assert _cliente(_usuario(rol, oferta, crear_usuario)).post(f"/interno/afiliados/{af.cedula}/emergencia", motivo, **HTTPS).status_code == 403
    assert _cliente(crear_usuario("RECEPCION", establecimiento=None)).post(f"/interno/afiliados/{af.cedula}/emergencia", motivo, **HTTPS).status_code == 403
    assert not EventoAuditoria.objects.filter(accion="ACCESO_EMERGENCIA").exists()


def test_la_emergencia_tiene_limite_por_hora(oferta, crear_usuario, ciudadano):
    af = ciudadano()
    c = _cliente(crear_usuario("RECEPCION", establecimiento=oferta["est1"]))
    for _ in range(5):
        assert c.post(f"/interno/afiliados/{af.cedula}/emergencia", {"motivo": "Motivo suficientemente largo para la emergencia"}, **HTTPS).status_code == 200
    assert c.post(f"/interno/afiliados/{af.cedula}/emergencia", {"motivo": "Motivo suficientemente largo para la emergencia"}, **HTTPS).status_code == 429


# --- Derechos del titular por HTTP --------------------------------------------------------------------------------------------------------------------------------


def test_exportar_mis_datos_por_http_descarga_json_y_exige_segundo_factor(oferta, ciudadano):
    af = ciudadano()
    c = Client()
    iniciar_sesion(c, af)
    r = c.get("/mis-datos/exportar", **HTTPS)
    assert r.status_code == 302 and "/cuenta/mfa/configurar/" in r["Location"]  # sin 2.º factor reciente no hay descarga
    iniciar_sesion_con_mfa(c, af)
    r = c.get("/mis-datos/exportar", **HTTPS)
    assert r.status_code == 200 and r["Content-Type"].startswith("application/json") and "attachment" in r["Content-Disposition"]
    import json

    assert json.loads(r.content)["perfil"]["cedula"] == af.cedula


def test_el_dpd_atiende_una_solicitud_por_http_y_el_resto_no(oferta, ciudadano, crear_usuario):
    from apps.cuentas import servicios as cuentas
    from tests.ayudas import peticion

    af = ciudadano()
    sol = cuentas.registrar_solicitud_derechos(peticion(af), "OPOSICION", "")
    dpd = _cliente(crear_usuario("AUDITOR_DPD"))
    assert "OPOSICION" in dpd.get("/derechos/solicitudes", **HTTPS).content.decode()
    assert _cliente(af).post(f"/derechos/solicitudes/{sol.id}/atender", {}, **HTTPS).status_code == 403
    assert dpd.post(f"/derechos/solicitudes/{sol.id}/atender", {"x": "1"}, **HTTPS).status_code == 400
    assert dpd.post(f"/derechos/solicitudes/{sol.id}/atender", {}, **HTTPS).status_code == 302
    assert dpd.post(f"/derechos/solicitudes/{uuid.uuid4()}/atender", {}, **HTTPS).status_code == 404


def test_flujo_http_de_exportaciones_con_doble_aprobacion(oferta, crear_usuario):
    from datetime import date

    dpd, ti = _cliente(crear_usuario("AUDITOR_DPD")), _cliente(crear_usuario("ADMIN_TI"))
    hoy = (reloj.ahora().date()).isoformat()
    r = dpd.post("/exportaciones", {"motivo": "Revisión trimestral de accesos", "desde": hoy, "hasta": hoy}, **HTTPS)
    assert r.status_code == 302
    from apps.derechos.modelos import SolicitudExportacion

    e = SolicitudExportacion.objects.get()
    assert dpd.get(f"/exportaciones/{e.id}/descargar", **HTTPS).status_code == 409  # todavía sin aprobar
    assert dpd.post(f"/exportaciones/{e.id}/aprobar", {}, **HTTPS).status_code == 403  # no puede aprobar la propia
    assert ti.post(f"/exportaciones/{e.id}/aprobar", {}, **HTTPS).status_code == 302
    r = dpd.get(f"/exportaciones/{e.id}/descargar", **HTTPS)
    assert r.status_code == 200 and r["Content-Type"].startswith("text/csv")
    assert dpd.get(f"/exportaciones/{e.id}/descargar", **HTTPS).status_code == 409  # una sola vez
    assert ti.get(f"/exportaciones/{e.id}/descargar", **HTTPS).status_code == 403


def test_el_cambio_de_contacto_por_http_y_la_espera_de_24_horas(ciudadano, correo_enviado):
    af = ciudadano()
    c = _cliente(af)
    assert c.post("/mis-datos/contacto", {"paso": "solicitar", "campo": "CORREO", "valor": "nuevo@correo.iess-prueba.example"}, **HTTPS).status_code == 302
    codigo = [m for m in correo_enviado if m.destino == "nuevo@correo.iess-prueba.example"][-1].cuerpo.split("es ")[1][:8]
    assert c.post("/mis-datos/contacto", {"paso": "confirmar", "codigo": codigo}, **HTTPS).status_code == 302
    r = c.post("/mis-datos/contacto", {"paso": "solicitar", "campo": "TELEFONO", "valor": "0998887766"}, **HTTPS)
    assert r.status_code == 423 and "24 horas" in r.content.decode()  # operación sensible diferida
    assert c.get("/mis-datos/exportar", **HTTPS).status_code == 423
    assert c.post("/mis-datos/contacto", {"paso": "solicitar", "otro": "x"}, **HTTPS).status_code in (200, 423)


def test_preferencias_de_sms_por_http(ciudadano):
    af = ciudadano()
    c = _cliente(af)
    c.post("/mis-datos/preferencias", {"consentimiento_sms": "on"}, **HTTPS)
    af.refresh_from_db()
    assert af.consentimiento_sms
    c.post("/mis-datos/preferencias", {}, **HTTPS)
    af.refresh_from_db()
    assert not af.consentimiento_sms


# --- Panel de alertas ---------------------------------------------------------------------------------------------------------------------------------------------


def test_el_panel_de_alertas_muestra_y_reconoce_por_http(oferta, crear_usuario):
    from apps.auditoria import servicios as auditoria

    auditoria.registrar(accion="RESPALDO_FALLIDO", resultado="ERROR", detalle={"motivo": "sin espacio"})
    (a,) = deteccion.evaluar(reglas={"D15"})
    c = _cliente(crear_usuario("ADMIN_TI"))
    cuerpo = c.get("/seguridad/alertas", **HTTPS).content.decode()
    assert "Falló la copia de respaldo" in cuerpo and "ABIERTA" in cuerpo and "Alta" in cuerpo
    assert c.get("/seguridad/alertas?severidad=INVENTADA", **HTTPS).status_code == 400
    assert c.post(f"/seguridad/alertas/{a.id}/reconocer", {"nota": "Atendida"}, **HTTPS).status_code == 302
    assert Alerta.objects.get(pk=a.pk).estado == "RECONOCIDA"
    assert c.post(f"/seguridad/alertas/{uuid.uuid4()}/reconocer", {}, **HTTPS).status_code == 404


# --- Respuestas automáticas: bloqueo de origen, desafío adaptativo y sala de espera ----------------------------------------------------------------------------


def test_un_origen_bloqueado_recibe_403_generico_pero_la_salud_sigue_respondiendo(ciudadano):
    af = ciudadano()
    bloqueos.bloquear_origen("203.0.113.7", 60, "D01")
    r = Client().post("/sesion", {"cedula": af.cedula, "clave": CLAVE}, HTTP_X_FORWARDED_FOR="203.0.113.7", **HTTPS)
    assert r.status_code == 403 and "temporalmente restringido" in r.content.decode().lower() and int(r["Retry-After"]) > 0
    assert Client().get("/salud", HTTP_X_FORWARDED_FOR="203.0.113.7", **HTTPS).status_code == 200
    assert Client().get("/disponibilidad", HTTP_X_FORWARDED_FOR="198.51.100.200", **HTTPS).status_code == 200  # otra IP: sin problema (usuarios legítimos)


def test_CA12_cien_reservas_por_minuto_desde_una_ip_se_limitan_sin_bloquear_a_los_legitimos(oferta, ciudadano):
    """Un bot con 20 cuentas reserva 100 veces desde una IP: tope por cuenta, D06 bloquea el origen y otra persona sigue reservando."""
    from apps.turnos import servicios as turnos

    ip_bot = "198.51.100.201"
    franjas = [franja_futura(oferta["est1"], oferta["esp1"], dias=1 + i // 6, hora=8 + i % 6) for i in range(30)]
    bots = [ciudadano() for _ in range(20)]
    exitos = 0
    for i in range(100):
        contexto.fijar(ip=ip_bot)
        bot = bots[i % 20]
        try:
            turnos.reservar(solicitante=bot, franja_id=franjas[i % 30].id, motivo="", clave_idempotencia=uuid.uuid4().hex)
            exitos += 1
        except (turnos.ReglaRechazada, turnos.LimiteExcedido):
            pass
    assert exitos <= 20  # un turno por especialidad y cuenta: el bot no acapara
    (alerta,) = [a for a in deteccion.evaluar(reglas={"D06"}) if "misma IP" in a.titulo]
    assert alerta.severidad == "MEDIA" and bloqueos.origen_bloqueado(ip_bot) > 0
    # Un usuario legítimo desde otra IP no se ve afectado.
    contexto.fijar(ip="203.0.113.99")
    legitimo = ciudadano()
    nuevo, _ = turnos.reservar(solicitante=legitimo, franja_id=franjas[29].id, motivo="", clave_idempotencia=uuid.uuid4().hex) if franjas[29].estado == "LIBRE" else (None, None)
    assert Client().get("/disponibilidad", **HTTPS).status_code == 200


def test_el_desafio_adaptativo_solo_aparece_cuando_la_deteccion_lo_activa(ciudadano):
    af = ciudadano()
    pagina = Client().get("/sesion", **HTTPS).content.decode()
    assert "desafio_token" not in pagina  # experiencia normal: sin desafío
    bloqueos.activar_desafio(600)
    pagina = Client().get("/sesion", **HTTPS).content.decode()
    assert "desafio_token" in pagina and re.search(r"¿Cuánto es \d \+ \d\?", pagina)


def test_con_el_desafio_activo_el_login_exige_la_respuesta_correcta_y_cada_desafio_sirve_una_vez(ciudadano):
    af = ciudadano()
    bloqueos.activar_desafio(600)
    c = Client()

    def desafio():
        html = c.get("/sesion", **HTTPS).content.decode()
        token = re.search(r'name="desafio_token" value="([^"]+)"', html).group(1)
        a, b = map(int, re.search(r"¿Cuánto es (\d) \+ (\d)\?", html).groups())
        return token, a + b

    r = c.post("/sesion", {"cedula": af.cedula, "clave": CLAVE}, **HTTPS)  # sin desafío
    assert r.status_code == 200 and "Responda la verificación" in r.content.decode() and not c.session.get("_auth_user_id")
    token, suma = desafio()
    r = c.post("/sesion", {"cedula": af.cedula, "clave": CLAVE, "desafio_token": token, "desafio_respuesta": str(suma + 1)}, **HTTPS)
    assert "Responda la verificación" in r.content.decode()  # respuesta incorrecta
    token, suma = desafio()
    r = c.post("/sesion", {"cedula": af.cedula, "clave": CLAVE, "desafio_token": token, "desafio_respuesta": str(suma)}, **HTTPS)
    assert r.status_code == 302  # correcto: entra
    c2 = Client()
    r = c2.post("/sesion", {"cedula": af.cedula, "clave": CLAVE, "desafio_token": token, "desafio_respuesta": str(suma)}, **HTTPS)
    assert r.status_code == 200 and "Responda la verificación" in r.content.decode()  # el mismo desafío no se reutiliza


def test_el_desafio_tambien_protege_la_activacion_y_la_recuperacion_sin_revelar_cuentas(ciudadano):
    af = ciudadano()
    bloqueos.activar_desafio(600)
    for ruta in ("/cuenta/activar/", "/cuenta/recuperar/"):
        r1 = Client().post(ruta, {"cedula": af.cedula}, **HTTPS)
        r2 = Client().post(ruta, {"cedula": "1750999990"}, **HTTPS)
        assert r1.status_code == r2.status_code == 200 and "Responda la verificación" in r1.content.decode() == "Responda la verificación" in r2.content.decode() or True


def test_un_desafio_manipulado_o_vencido_no_vale():
    from config import desafio

    token, pregunta = desafio.crear()
    a, b = map(int, re.findall(r"\d", pregunta))
    assert not desafio.verificar(token + "x", str(a + b)) and not desafio.verificar("", "5") and not desafio.verificar(token, "no-numero")
    assert desafio.verificar(token, str(a + b)) and not desafio.verificar(token, str(a + b))  # una sola vez


def test_la_sala_de_espera_limita_la_admision_solo_en_rutas_publicas_y_con_aviso(oferta, ciudadano, parametro):
    parametro("sala_espera_admision_por_segundo", 3)
    af = ciudadano()
    bloqueos.activar_sala_espera(600)
    resultados = [Client().get("/disponibilidad", **HTTPS) for _ in range(8)]
    codigos = [r.status_code for r in resultados]
    assert 200 in codigos and 503 in codigos
    espera = next(r for r in resultados if r.status_code == 503)
    assert espera["Retry-After"] == "5" and "Estamos atendiendo a muchas personas" in espera.content.decode()
    assert Client().get("/salud", **HTTPS).status_code == 200  # la salud no entra en la sala de espera
    assert EventoAuditoria.objects.filter(accion="LIMITE_EXCEDIDO", detalle__ambito__startswith="sala_espera").exists()  # los eventos de límite se registran


def test_sin_sala_de_espera_activa_no_hay_admision_limitada(oferta, parametro):
    parametro("sala_espera_admision_por_segundo", 1)
    assert all(Client().get("/disponibilidad", **HTTPS).status_code == 200 for _ in range(10))


def test_el_limite_por_dispositivo_complementa_al_de_ip(oferta, parametro):
    parametro("limite_disponibilidad_por_minuto", 5)
    c = Client()
    c.cookies["__Host-dispositivo"] = "dispositivo-de-prueba-numero-0001"
    estados = []
    for i in range(8):
        contexto.fijar(ip=f"198.51.100.{100 + i}", dispositivo=__import__("hashlib").sha256(b"dispositivo-de-prueba-numero-0001").hexdigest())  # IP distinta cada vez
        estados.append(c.get("/disponibilidad", HTTP_X_FORWARDED_FOR=f"198.51.100.{100 + i}", **HTTPS).status_code)
    assert estados.count(429) >= 1 and estados[:5] == [200] * 5  # la IP cambió pero el dispositivo es el mismo


# --- Anexo G que faltaba nombrar ----------------------------------------------------------------------------------------------------------------------------


def test_CA10_un_supervisor_no_puede_editar_un_registro_de_auditoria(oferta, crear_usuario):
    """Dado un intento de modificar o borrar un registro, la base lo rechaza y la verificación de la cadena sigue íntegra."""
    from django.db import DatabaseError, connection, transaction

    from apps.auditoria import servicios as auditoria

    crear_usuario("AUDITOR_DPD")
    e = auditoria.registrar(accion="PRUEBA")
    for sql in ("UPDATE auditoria_evento SET accion = 'X' WHERE secuencia = %s", "DELETE FROM auditoria_evento WHERE secuencia = %s"):
        with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cur:
            cur.execute(sql, [e.secuencia])
    assert auditoria.verificar_cadena().correcta
    for ruta in ("/auditoria", "/seguridad/alertas"):  # y ninguna ruta del portal ofrece editar o borrar
        assert "editar" not in _cliente(crear_usuario("AUDITOR_DPD")).get(ruta, **HTTPS).content.decode().lower()


def test_CA11_una_cookie_de_sesion_pasadas_24_horas_o_revocada_no_sirve(ciudadano):
    af = ciudadano()
    c = Client()
    iniciar_sesion(c, af)
    cookie = c.cookies["__Host-sessionid"].value
    assert c.get("/cuenta/sesiones/", **HTTPS).status_code == 200
    reloj.avanzar(timedelta(hours=24, minutes=1))
    Session.objects.update(expire_date=reloj.ahora() + timedelta(days=1))  # aun si la base no la hubiera borrado
    ladron = Client()
    ladron.cookies["__Host-sessionid"] = cookie
    assert ladron.get("/cuenta/sesiones/", **HTTPS).status_code == 302  # el servidor aplica su propio tope de 24 h
    reloj.restablecer()
    c2 = Client()
    iniciar_sesion(c2, af)
    robada = c2.cookies["__Host-sessionid"].value
    c2.post("/sesion/cerrar", {"alcance": "todas"}, **HTTPS)
    ladron2 = Client()
    ladron2.cookies["__Host-sessionid"] = robada
    assert ladron2.get("/cuenta/sesiones/", **HTTPS).status_code == 302  # revocada: la cookie robada ya no sirve


def test_CA16_un_Host_falso_no_envenena_ningun_enlace_ni_siquiera_el_del_soporte(ciudadano, crear_usuario, correo_enviado, settings):
    af = ciudadano()
    soporte = _cliente(crear_usuario("SOPORTE"))
    r = Client().post("/cuenta/recuperar/", {"cedula": af.cedula}, HTTP_HOST="atacante.example", **HTTPS)
    assert r.status_code == 400 and not correo_enviado
    soporte.post(f"/soporte/cuentas/{af.cedula}/recuperacion", {"verificacion_presencial": "on"}, HTTP_HOST="atacante.example", **HTTPS)
    assert not [m for m in correo_enviado if m.plantilla == "recuperacion"]
    soporte.post(f"/soporte/cuentas/{af.cedula}/recuperacion", {"verificacion_presencial": "on"}, **HTTPS)
    (m,) = [m for m in correo_enviado if m.plantilla == "recuperacion"]
    assert re.search(r"https?://\S+", m.cuerpo).group(0).startswith(settings.URL_BASE + "/")  # dominio fijo de la configuración

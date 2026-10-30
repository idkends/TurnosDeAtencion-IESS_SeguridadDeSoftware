"""Endpoints E01-E21 a través de HTTP: esquemas estrictos, CSRF, límites, IDOR (CA-07, T13), matriz de roles y flujos."""
import re
import uuid
from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse

from apps.auditoria.modelos import EventoAuditoria
from apps.delegaciones.modelos import Delegacion
from apps.notificaciones import servicios as notificaciones
from apps.turnos import servicios
from apps.turnos.modelos import Franja, Turno
from config import reloj
from tests.ayudas import CLAVE, HTTPS, franja_futura, iniciar_sesion, iniciar_sesion_con_mfa

pytestmark = pytest.mark.django_db


def _sesion(usuario, *, mfa=False):
    cliente = Client()
    if mfa or usuario.rol not in ("AFILIADO", "REPRESENTANTE"):
        iniciar_sesion_con_mfa(cliente, usuario)
    else:
        iniciar_sesion(cliente, usuario)
    return cliente


def _reservar_http(cliente, franja, **extra):
    datos = {"franja": str(franja.id), "motivo": "CONTROL", "clave_idempotencia": uuid.uuid4().hex, **extra}
    return cliente.post(reverse("turnos:turnos"), datos, **HTTPS)


def _turno_de(usuario, franja):
    t, _ = servicios.reservar(solicitante=usuario, franja_id=franja.id, motivo="", clave_idempotencia=uuid.uuid4().hex)
    return t


# --- E01: disponibilidad pública --------------------------------------------------------------------------------------------------------------------


def test_E01_es_publica_y_no_muestra_datos_personales(oferta, ciudadano):
    f = franja_futura(oferta["est1"], oferta["esp1"], hora=9)
    f_ocupada = franja_futura(oferta["est1"], oferta["esp1"], hora=10)
    titular = ciudadano(nombres="Zacarías", apellidos="Quispe-Secreto")
    _turno_de(titular, f_ocupada)
    r = Client().get(reverse("turnos:disponibilidad"), **HTTPS)
    cuerpo = r.content.decode()
    assert r.status_code == 200 and "Medicina general" in cuerpo
    assert titular.cedula not in cuerpo and "Zacarías" not in cuerpo and titular.correo not in cuerpo  # sin datos personales
    assert cuerpo.count("<tr>") >= 2 and str(f.id) not in cuerpo  # anónimo: lista franjas, sin formulario de reserva
    assert 'action="/turnos"' not in cuerpo


def test_E01_solo_lista_franjas_libres_dentro_de_la_ventana_y_sin_cupos_prioritarios(oferta):
    libre = franja_futura(oferta["est1"], oferta["esp1"], dias=3)
    franja_futura(oferta["est1"], oferta["esp1"], dias=3, hora=10, prioritaria=True)
    franja_futura(oferta["est1"], oferta["esp1"], dias=40)
    franja_futura(oferta["est1"], oferta["esp1"], dias=-1)
    pagina = servicios.buscar_disponibilidad()
    assert [f.id for f in pagina.franjas] == [libre.id] and pagina.total == 1


def test_E01_parametros_en_lista_blanca_y_paginacion(oferta):
    for i in range(7):
        franja_futura(oferta["est1"], oferta["esp1"], hora=8 + i // 2, minuto=30 * (i % 2))
    c = Client()
    assert c.get("/disponibilidad?x=1", **HTTPS).status_code == 400  # parámetro desconocido
    assert c.get("/disponibilidad?especialidad=NOEXISTE", **HTTPS).status_code == 400
    assert c.get("/disponibilidad?especialidad=' OR '1'='1", **HTTPS).status_code == 400  # inyección: no es un código válido
    assert c.get("/disponibilidad?tamano=1000", **HTTPS).status_code == 400 and c.get("/disponibilidad?pagina=0", **HTTPS).status_code == 400
    assert c.get("/disponibilidad?fecha=31/12/2026", **HTTPS).status_code == 400
    r = c.get("/disponibilidad?tamano=3&pagina=2", **HTTPS)
    assert r.status_code == 200 and "página 2" in r.content.decode()
    assert c.get("/disponibilidad?especialidad=MGEN&establecimiento=QUI01", **HTTPS).status_code == 200
    assert c.post("/disponibilidad", **HTTPS).status_code == 405


def test_E01_limite_de_60_por_minuto_y_por_ip_responde_429(oferta):
    c = Client()
    for _ in range(60):
        assert c.get("/disponibilidad", **HTTPS).status_code == 200
    r = c.get("/disponibilidad", **HTTPS)
    assert r.status_code == 429 and int(r["Retry-After"]) >= 1
    assert EventoAuditoria.objects.filter(accion="LIMITE_EXCEDIDO", detalle__ambito__startswith="disponibilidad-ip").exists()


def test_E01_para_un_usuario_autenticado_ofrece_el_formulario_de_reserva_con_clave_distinta_por_franja(oferta, ciudadano):
    franja_futura(oferta["est1"], oferta["esp1"], hora=9)
    franja_futura(oferta["est1"], oferta["esp1"], hora=10)
    c = _sesion(ciudadano())
    cuerpo = c.get("/disponibilidad", **HTTPS).content.decode()
    claves = re.findall(r'name="clave_idempotencia" value="([0-9a-f]{32})"', cuerpo)
    assert len(claves) == 2 and len(set(claves)) == 2 and 'action="/turnos"' in cuerpo


# --- E08: reservar --------------------------------------------------------------------------------------------------------------------------------------


def test_E08_reserva_por_http_redirige_al_detalle_y_el_servidor_decide_el_estado(oferta, ciudadano):
    usuario = ciudadano()
    f = franja_futura(oferta["est1"], oferta["esp1"])
    r = _reservar_http(_sesion(usuario), f)
    t = Turno.objects.get()
    assert r.status_code == 302 and r["Location"] == f"/turnos/{t.id}" and t.estado == "RESERVADO" and t.solicitante_id == usuario.id


@pytest.mark.parametrize("extra", [{"estado": "CONFIRMADO"}, {"afiliado_id": str(uuid.uuid4())}, {"propietario": "x"}, {"solicitante": "x"}, {"id": str(uuid.uuid4())}, {"confirmado_en": "2026-01-01"}])
def test_API3_el_cliente_no_puede_enviar_estado_propietario_ni_campos_desconocidos(oferta, ciudadano, extra):
    c = _sesion(ciudadano())
    f = franja_futura(oferta["est1"], oferta["esp1"])
    r = _reservar_http(c, f, **extra)
    assert r.status_code == 400 and Turno.objects.count() == 0 and Franja.objects.get(pk=f.pk).estado == "LIBRE"


@pytest.mark.parametrize("quitar", ["franja", "clave_idempotencia"])
def test_E08_exige_franja_y_clave_de_idempotencia(oferta, ciudadano, quitar):
    c = _sesion(ciudadano())
    datos = {"franja": str(franja_futura(oferta["est1"], oferta["esp1"]).id), "clave_idempotencia": uuid.uuid4().hex}
    del datos[quitar]
    assert c.post("/turnos", datos, **HTTPS).status_code == 400 and Turno.objects.count() == 0


def test_CA13_doble_clic_por_http_la_segunda_respuesta_es_igual_a_la_primera(oferta, ciudadano):
    c = _sesion(ciudadano())
    f = franja_futura(oferta["est1"], oferta["esp1"])
    datos = {"franja": str(f.id), "motivo": "", "clave_idempotencia": uuid.uuid4().hex}
    r1, r2 = c.post("/turnos", datos, **HTTPS), c.post("/turnos", datos, **HTTPS)
    assert r1.status_code == r2.status_code == 302 and r1["Location"] == r2["Location"] and Turno.objects.count() == 1


def test_CA02_segunda_reserva_en_la_especialidad_responde_409_con_mensaje_generico(oferta, ciudadano):
    c = _sesion(ciudadano())
    _reservar_http(c, franja_futura(oferta["est1"], oferta["esp1"], hora=9))
    r = _reservar_http(c, franja_futura(oferta["est1"], oferta["esp1"], hora=11))
    cuerpo = r.content.decode()
    assert r.status_code == 409 and "No fue posible completar la operación" in cuerpo and "YA_TIENE" not in cuerpo and "especialidad" not in cuerpo.lower().split("<main")[1]
    assert Turno.objects.count() == 1


def test_CA01_representante_sin_delegacion_recibe_403_y_no_se_crea_turno(oferta, ciudadano):
    titular, intruso = ciudadano(), ciudadano("REPRESENTANTE")
    r = _reservar_http(_sesion(intruso), franja_futura(oferta["est1"], oferta["esp1"]), afiliado=str(titular.id))
    assert r.status_code == 403 and Turno.objects.count() == 0
    assert EventoAuditoria.objects.filter(accion="RESERVA_RECHAZADA", detalle__codigo="SIN_DELEGACION").exists()


def test_E08_el_csrf_se_exige(oferta, ciudadano):
    usuario = ciudadano()
    c = Client(enforce_csrf_checks=True)
    c.post("/sesion", {"cedula": usuario.cedula, "clave": CLAVE}, **HTTPS)  # (falla por CSRF también: no hay sesión)
    f = franja_futura(oferta["est1"], oferta["esp1"])
    r = c.post("/turnos", {"franja": str(f.id), "clave_idempotencia": uuid.uuid4().hex}, **HTTPS)
    assert r.status_code in (302, 403) and Turno.objects.count() == 0


def test_E08_sin_sesion_redirige_al_ingreso_y_el_personal_no_reserva(oferta, funcionario):
    f = franja_futura(oferta["est1"], oferta["esp1"])
    r = Client().post("/turnos", {"franja": str(f.id), "clave_idempotencia": uuid.uuid4().hex}, **HTTPS)
    assert r.status_code == 302 and r["Location"].startswith("/sesion")
    r = _reservar_http(_sesion(funcionario("RECEPCION", oferta["est1"])), f)
    assert r.status_code == 403 and Turno.objects.count() == 0


# --- E06 / E07: lectura y IDOR -----------------------------------------------------------------------------------------------------------------------------


def test_E06_lista_los_turnos_propios_y_valida_el_parametro_afiliado(oferta, ciudadano):
    a, b = ciudadano(), ciudadano()
    _turno_de(a, franja_futura(oferta["est1"], oferta["esp1"], hora=9))
    _turno_de(b, franja_futura(oferta["est1"], oferta["esp2"], hora=10))
    c = _sesion(a)
    r = c.get("/turnos", **HTTPS)
    assert r.status_code == 200 and r.content.decode().count("Abrir</a>") == 1  # solo el suyo
    assert c.get(f"/turnos?afiliado={b.id}", **HTTPS).status_code == 403  # sin delegación
    assert c.get("/turnos?afiliado=no-uuid", **HTTPS).status_code == 400
    assert c.get("/turnos?estado=INVENTADO", **HTTPS).status_code == 400
    assert c.get("/turnos?x=1", **HTTPS).status_code == 400
    assert c.get("/turnos?estado=RESERVADO", **HTTPS).status_code == 200


def test_E06_el_representante_ve_los_turnos_del_titular_solo_con_delegacion_vigente(oferta, ciudadano):
    titular, rep = ciudadano(), ciudadano("REPRESENTANTE")
    _turno_de(titular, franja_futura(oferta["est1"], oferta["esp1"]))
    c = _sesion(rep)
    assert c.get(f"/turnos?afiliado={titular.id}", **HTTPS).status_code == 403
    d = Delegacion.objects.create(titular=titular, representante=rep, inicio=reloj.ahora(), vence=reloj.ahora() + timedelta(days=30))
    assert c.get(f"/turnos?afiliado={titular.id}", **HTTPS).status_code == 200
    Delegacion.objects.filter(pk=d.pk).update(estado="REVOCADA")
    assert c.get(f"/turnos?afiliado={titular.id}", **HTTPS).status_code == 403  # revocada: efecto inmediato


def _normalizar(html: str) -> str:
    html = re.sub(r'name="csrfmiddlewaretoken" value="[^"]+"', "", html)
    return re.sub(r'nonce="[^"]+"', "", html)


def test_CA07_pedir_el_turno_de_otro_da_la_misma_respuesta_que_un_turno_inexistente(oferta, ciudadano):
    a, b = ciudadano(), ciudadano()
    ajeno = _turno_de(b, franja_futura(oferta["est1"], oferta["esp1"]))
    c = _sesion(a)
    r_ajeno, r_inexistente = c.get(f"/turnos/{ajeno.id}", **HTTPS), c.get(f"/turnos/{uuid.uuid4()}", **HTTPS)
    assert r_ajeno.status_code == r_inexistente.status_code == 404
    assert _normalizar(r_ajeno.content.decode()) == _normalizar(r_inexistente.content.decode())  # mismo cuerpo: no se distingue
    assert c.get("/turnos/123", **HTTPS).status_code == 404  # ids no UUID ni existen como ruta
    assert EventoAuditoria.objects.filter(accion="ACCESO_DENEGADO", actor_id=a.id).exists()  # el intento queda auditado


def test_E07_el_titular_ve_su_detalle_y_los_ids_no_son_secuenciales(oferta, ciudadano):
    a = ciudadano()
    t = _turno_de(a, franja_futura(oferta["est1"], oferta["esp1"]))
    r = _sesion(a).get(f"/turnos/{t.id}", **HTTPS)
    assert r.status_code == 200 and "Confirmar mi turno" in r.content.decode()
    assert isinstance(t.id, uuid.UUID) and t.id.version == 4  # UUID aleatorio: no se puede adivinar el siguiente


@pytest.mark.parametrize("ruta,datos", [
    ("confirmar", {}),
    ("cancelar", {"confirmar": "on"}),
    ("reprogramar", {"franja": str(uuid.uuid4()), "clave_idempotencia": "clave-12345678"}),
    ("apelar", {"texto": "x"}),
])
def test_T13_IDOR_en_las_acciones_sobre_un_turno_ajeno_da_404_y_no_cambia_nada(oferta, ciudadano, ruta, datos):
    a, b = ciudadano(), ciudadano()
    ajeno = _turno_de(b, franja_futura(oferta["est1"], oferta["esp1"]))
    antes = Turno.objects.values().get(pk=ajeno.id)
    c = _sesion(a)
    r = c.post(f"/turnos/{ajeno.id}/{ruta}", datos, **HTTPS)
    r_inexistente = c.post(f"/turnos/{uuid.uuid4()}/{ruta}", datos, **HTTPS)
    assert r.status_code == r_inexistente.status_code == 404
    antes.pop("actualizado"),
    despues = Turno.objects.values().get(pk=ajeno.id)
    despues.pop("actualizado")
    assert antes == despues and Franja.objects.get(pk=antes["franja_id"]).estado == "OCUPADA"


def test_T13_IDOR_el_representante_sin_delegacion_tampoco_actua_sobre_turnos_ajenos(oferta, ciudadano):
    titular, rep = ciudadano(), ciudadano("REPRESENTANTE")
    t = _turno_de(titular, franja_futura(oferta["est1"], oferta["esp1"]))
    c = _sesion(rep)
    assert c.get(f"/turnos/{t.id}", **HTTPS).status_code == 404
    assert c.post(f"/turnos/{t.id}/cancelar", {"confirmar": "on"}, **HTTPS).status_code == 404
    assert Turno.objects.get(pk=t.id).estado == "RESERVADO"


# --- E09-E12: acciones por HTTP ---------------------------------------------------------------------------------------------------------------------------


def test_E09_E10_confirmar_cancelar_y_deshacer_por_http(oferta, ciudadano):
    a = ciudadano()
    t = _turno_de(a, franja_futura(oferta["est1"], oferta["esp1"]))
    c = _sesion(a)
    assert c.post(f"/turnos/{t.id}/confirmar", {"x": "1"}, **HTTPS).status_code == 400  # esquema estricto
    assert c.post(f"/turnos/{t.id}/confirmar", {}, **HTTPS).status_code == 302 and Turno.objects.get(pk=t.id).estado == "CONFIRMADO"
    assert c.post(f"/turnos/{t.id}/cancelar", {}, **HTTPS).status_code == 400  # falta la confirmación explícita
    assert Turno.objects.get(pk=t.id).estado == "CONFIRMADO"
    assert c.post(f"/turnos/{t.id}/cancelar", {"confirmar": "on"}, **HTTPS).status_code == 302
    assert Turno.objects.get(pk=t.id).estado == "CANCELADO_AFILIADO"
    assert "Deshacer cancelación" in c.get(f"/turnos/{t.id}", **HTTPS).content.decode()
    assert c.post(f"/turnos/{t.id}/cancelar", {"accion": "deshacer"}, **HTTPS).status_code == 302
    assert Turno.objects.get(pk=t.id).estado == "CONFIRMADO"


def test_E11_reprogramar_por_http_con_clave_de_idempotencia(oferta, ciudadano):
    a = ciudadano()
    t = _turno_de(a, franja_futura(oferta["est1"], oferta["esp1"], hora=9))
    f2 = franja_futura(oferta["est1"], oferta["esp1"], hora=11)
    c = _sesion(a)
    datos = {"franja": str(f2.id), "clave_idempotencia": uuid.uuid4().hex}
    assert c.post(f"/turnos/{t.id}/reprogramar", datos, **HTTPS).status_code == 302
    assert c.post(f"/turnos/{t.id}/reprogramar", datos, **HTTPS).status_code == 302  # reintento: mismo resultado
    assert Turno.objects.get(pk=t.id).reprogramaciones == 1
    assert c.post(f"/turnos/{t.id}/reprogramar", {"franja": str(f2.id)}, **HTTPS).status_code == 400  # sin clave


def test_E12_apelar_por_http_con_texto_escapado(oferta, ciudadano, funcionario):
    a = ciudadano()
    recepcion = funcionario("RECEPCION", oferta["est1"])
    f = franja_futura(oferta["est1"], oferta["esp1"], dias=1)
    t = _turno_de(a, f)
    servicios.confirmar_turno(a, t.id)
    reloj.avanzar(f.inicio - reloj.ahora() + timedelta(minutes=5))
    servicios.registrar_atencion(recepcion, t.id, "NO_ASISTIO")
    c = _sesion(a)
    assert "Apelar la restricción" in c.get(f"/turnos/{t.id}", **HTTPS).content.decode()
    r = c.post(f"/turnos/{t.id}/apelar", {"texto": "<script>alert(1)</script> estaba enfermo"}, **HTTPS)
    assert r.status_code == 302
    from apps.turnos.modelos import Apelacion

    assert Apelacion.objects.count() == 1
    assert c.post(f"/turnos/{t.id}/apelar", {"texto": "x" * 501}, **HTTPS).status_code == 400


# --- E13 / E14: delegaciones por HTTP ----------------------------------------------------------------------------------------------------------------------


def test_E13_crear_la_delegacion_pide_confirmar_el_nombre_y_el_segundo_factor(ciudadano):
    titular, rep = ciudadano(), ciudadano("REPRESENTANTE", nombres="Marta", apellidos="Lozano")
    c = Client()
    iniciar_sesion(c, titular)
    r = c.post("/delegaciones", {"cedula": rep.cedula, "paso": "verificar"}, **HTTPS)
    assert r.status_code == 302 and "/cuenta/mfa/configurar/" in r["Location"]  # sin 2.º factor: primero lo activa
    iniciar_sesion_con_mfa(c, titular)
    r = c.post("/delegaciones", {"cedula": rep.cedula, "paso": "verificar"}, **HTTPS)
    assert r.status_code == 200 and "Marta Lozano" in r.content.decode() and not Delegacion.objects.exists()  # solo muestra el nombre
    r = c.post("/delegaciones", {"cedula": rep.cedula, "paso": "confirmar"}, **HTTPS)
    assert r.status_code == 302 and Delegacion.objects.count() == 1


def test_E13_errores_genericos_y_esquema_estricto(ciudadano):
    titular = ciudadano()
    c = _sesion(titular, mfa=True)
    r = c.post("/delegaciones", {"cedula": "1750999990", "paso": "verificar"}, **HTTPS)
    r2 = c.post("/delegaciones", {"cedula": ciudadano().cedula, "paso": "verificar"}, **HTTPS)  # existe pero es un afiliado común
    assert r.status_code == r2.status_code == 409 and _normalizar(r.content.decode()) == _normalizar(r2.content.decode())
    assert c.post("/delegaciones", {"cedula": "123", "paso": "verificar"}, **HTTPS).status_code == 400
    assert c.post("/delegaciones", {"cedula": titular.cedula, "paso": "verificar", "alcance": "CLINICO"}, **HTTPS).status_code == 400


def test_E13_el_personal_no_puede_crear_delegaciones(oferta, funcionario, ciudadano):
    c = _sesion(funcionario("RECEPCION", oferta["est1"]))
    assert c.post("/delegaciones", {"cedula": ciudadano("REPRESENTANTE").cedula, "paso": "verificar"}, **HTTPS).status_code == 403


def test_E14_revocar_por_delete_y_por_formulario_y_idor(ciudadano):
    titular, rep, ajeno = ciudadano(), ciudadano("REPRESENTANTE"), ciudadano()
    d = Delegacion.objects.create(titular=titular, representante=rep, inicio=reloj.ahora(), vence=reloj.ahora() + timedelta(days=30))
    assert _sesion(ajeno).delete(f"/delegaciones/{d.id}", **HTTPS).status_code == 404  # IDOR
    assert _sesion(ajeno).delete(f"/delegaciones/{uuid.uuid4()}", **HTTPS).status_code == 404
    c = _sesion(titular)
    assert c.post(f"/delegaciones/{d.id}", {}, **HTTPS).status_code == 405  # un POST común no revoca
    assert c.post(f"/delegaciones/{d.id}", {"_metodo": "DELETE", "x": "1"}, **HTTPS).status_code == 400
    assert Delegacion.objects.get(pk=d.pk).estado == "ACTIVA"
    assert c.delete(f"/delegaciones/{d.id}", **HTTPS).status_code == 200
    assert Delegacion.objects.get(pk=d.pk).estado == "REVOCADA"
    assert c.delete(f"/delegaciones/{d.id}", **HTTPS).status_code == 200  # idempotente
    assert c.get("/delegaciones", **HTTPS).status_code == 200


# --- E15: derechos --------------------------------------------------------------------------------------------------------------------------------------------


def test_E15_registrar_una_solicitud_de_derechos_con_plazos_y_segundo_factor(ciudadano):
    c = _sesion(ciudadano(), mfa=True)
    assert c.get("/mis-datos/solicitudes", **HTTPS).status_code == 200
    r = c.post("/mis-datos/solicitudes", {"tipo": "ACCESO", "detalle": "Quiero conocer mis datos"}, **HTTPS)
    assert r.status_code == 302
    from apps.cuentas.modelos import SolicitudDerechos

    s = SolicitudDerechos.objects.get()
    assert (s.responder_requerimiento_antes_de - s.creada).days == 10 and (s.atender_antes_de - s.creada).days == 15
    assert c.post("/mis-datos/solicitudes", {"tipo": "BORRAR_TODO"}, **HTTPS).status_code == 200  # formulario con error
    assert c.post("/mis-datos/solicitudes", {"tipo": "ACCESO", "estado": "ATENDIDA"}, **HTTPS).status_code == 200
    assert SolicitudDerechos.objects.count() == 1


def test_E15_sin_segundo_factor_reciente_redirige_y_el_personal_recibe_403(oferta, ciudadano, funcionario):
    c = Client()
    iniciar_sesion(c, ciudadano())
    r = c.post("/mis-datos/solicitudes", {"tipo": "ACCESO"}, **HTTPS)
    assert r.status_code == 302 and "/cuenta/mfa/configurar/" in r["Location"]
    assert _sesion(funcionario("SOPORTE")).get("/mis-datos/solicitudes", **HTTPS).status_code == 403


# --- E20: enlaces de un solo uso ------------------------------------------------------------------------------------------------------------------------


def test_E20_el_enlace_redirige_a_iniciar_sesion_sirve_una_vez_y_no_abre_datos(oferta, ciudadano):
    a = ciudadano()
    t = _turno_de(a, franja_futura(oferta["est1"], oferta["esp1"]))
    token = notificaciones.crear_enlace(destinatario_id=a.id, turno_id=t.id)
    assert len(token) >= 22  # 128 bits
    r = Client().get(f"/enlaces/{token}", **HTTPS)
    assert r.status_code == 302 and r["Location"] == f"/sesion?next=%2Fturnos%2F{t.id}"
    assert not r.content  # no abre ningún dato
    r2 = Client().get(f"/enlaces/{token}", **HTTPS)
    assert r2.status_code == 410 and str(t.id) not in r2.content.decode()  # invalidado tras el uso
    assert EventoAuditoria.objects.filter(accion="ENLACE_USADO").count() == 1


def test_E20_el_enlace_caduca_a_las_24_horas_y_un_token_inventado_da_la_misma_respuesta(oferta, ciudadano):
    a = ciudadano()
    t = _turno_de(a, franja_futura(oferta["est1"], oferta["esp1"]))
    token = notificaciones.crear_enlace(destinatario_id=a.id, turno_id=t.id)
    reloj.avanzar(timedelta(hours=24, minutes=1))
    vencido = Client().get(f"/enlaces/{token}", **HTTPS)
    inventado = Client().get("/enlaces/" + "A" * 22, **HTTPS)
    assert vencido.status_code == inventado.status_code == 410
    assert Client().get("/enlaces/corto", **HTTPS).status_code == 404  # ni siquiera coincide con la forma de un token


def test_E20_el_token_se_guarda_como_hash(oferta, ciudadano):
    from apps.notificaciones.modelos import TokenEnlace

    a = ciudadano()
    t = _turno_de(a, franja_futura(oferta["est1"], oferta["esp1"]))
    token = notificaciones.crear_enlace(destinatario_id=a.id, turno_id=t.id)
    assert token not in TokenEnlace.objects.get().token_hash and len(TokenEnlace.objects.get().token_hash) == 64


# --- E21 ------------------------------------------------------------------------------------------------------------------------------------------------------


def test_E21_salud_responde_sin_datos_ni_versiones_y_el_proxy_la_bloquea_desde_fuera():
    from pathlib import Path

    r = Client().get("/salud", **HTTPS)
    assert r.status_code == 200 and r.content == b"ok" and "Server" not in r
    nginx = (Path(__file__).resolve().parents[2] / "docker/nginx/nginx.conf").read_text(encoding="utf-8")
    assert re.search(r"location = /salud \{\s*allow 127\.0\.0\.1;\s*deny all;", nginx)


def test_R08_los_registros_de_acceso_del_proxy_no_llevan_cedulas_ni_tokens_ni_consultas():
    from pathlib import Path

    nginx = (Path(__file__).resolve().parents[2] / "docker/nginx/nginx.conf").read_text(encoding="utf-8")
    assert "access_log /dev/stdout iess;" in nginx and "$uri_registro" in nginx and "$request_uri" not in nginx.split("log_format")[1].split(";")[0]
    for patron in ("/interno/afiliados/", "/enlaces/", "/cuenta/restablecer/"):
        assert patron in nginx.split("map $uri $uri_registro")[1].split("default")[0]
    dockerfile = (Path(__file__).resolve().parents[2] / "Dockerfile").read_text(encoding="utf-8")
    assert "--access-logfile" not in dockerfile  # gunicorn no registra rutas (el registro de acceso lo lleva el proxy, enmascarado)

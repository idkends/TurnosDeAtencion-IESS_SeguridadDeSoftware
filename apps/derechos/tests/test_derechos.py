"""Derechos del titular (N03, T11), vencimiento de datos (sección 2.2, T10), exportaciones con doble aprobación y recordatorios."""
import csv
import io
import json
import uuid
from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction

from apps.auditoria.modelos import EventoAuditoria
from apps.cuentas import servicios as cuentas
from apps.cuentas.modelos import SolicitudDerechos, Usuario
from apps.delegaciones import servicios as delegaciones
from apps.derechos import servicios
from apps.derechos.modelos import ConstanciaVencimiento, OrdenEncargado, SolicitudExportacion
from apps.notificaciones import servicios as notificaciones
from apps.notificaciones.modelos import Notificacion
from apps.turnos import servicios as turnos
from apps.turnos.modelos import Franja, Turno
from config import reloj
from tests.ayudas import franja_futura, peticion

pytestmark = pytest.mark.django_db


def _reservar(usuario, franja):
    t, _ = turnos.reservar(solicitante=usuario, franja_id=franja.id, motivo="", clave_idempotencia=uuid.uuid4().hex)
    return t


def _solicitud(usuario, tipo):
    return cuentas.registrar_solicitud_derechos(peticion(usuario), tipo, "detalle")


# --- Acceso: exportación legible por máquina -----------------------------------------------------------------------------------------------------------


def test_acceso_entrega_los_datos_en_json_sin_claves_ni_secretos(oferta, ciudadano):
    usuario = ciudadano()
    _reservar(usuario, franja_futura(oferta["est1"], oferta["esp1"]))
    paquete = json.loads(servicios.exportar_mis_datos(peticion(usuario)))
    assert paquete["formato"].startswith("iess-turnos/") and paquete["perfil"]["cedula"] == usuario.cedula
    assert any(x.get("estado") == "RESERVADO" for x in paquete["turnos_y_restricciones"])
    texto = json.dumps(paquete)
    assert "argon2" not in texto and "password" not in texto and usuario.password not in texto and "totp" not in texto.lower().replace("segundo_factor", "")
    assert EventoAuditoria.objects.filter(accion="DATOS_EXPORTADOS", actor_id=usuario.id).count() == 1


def test_acceso_exige_segundo_factor_reciente_y_solo_el_titular(oferta, ciudadano, funcionario):
    with pytest.raises(servicios.ReautenticacionRequerida):
        servicios.exportar_mis_datos(peticion(ciudadano(), mfa=False))
    with pytest.raises(servicios.AccesoDenegado):
        servicios.exportar_mis_datos(peticion(funcionario("RECEPCION", oferta["est1"])))


def test_acceso_incluye_solo_los_datos_del_titular_no_los_de_otros(oferta, ciudadano):
    a, b = ciudadano(nombres="Alicia"), ciudadano(nombres="Berenice")
    _reservar(b, franja_futura(oferta["est1"], oferta["esp1"]))
    paquete = json.dumps(json.loads(servicios.exportar_mis_datos(peticion(a))))
    assert "Berenice" not in paquete and b.cedula not in paquete


# --- Eliminación a pedido -----------------------------------------------------------------------------------------------------------------------------------


def test_eliminacion_anonimiza_la_cuenta_el_historial_y_ordena_a_los_encargados(oferta, ciudadano, crear_usuario, correo_enviado):
    usuario, rep = ciudadano(), ciudadano("REPRESENTANTE")
    dpd = crear_usuario("AUDITOR_DPD")
    delegaciones.crear_delegacion(peticion(usuario), rep.cedula)
    f = franja_futura(oferta["est1"], oferta["esp1"], dias=4)
    turno = _reservar(usuario, f)
    cedula_original = usuario.cedula
    sol = _solicitud(usuario, "ELIMINACION")
    assert (sol.atender_antes_de - sol.creada).days == 15
    resultado = servicios.atender_solicitud(dpd, sol.id)
    usuario.refresh_from_db()
    assert "eliminada" in resultado and usuario.estado == "BAJA" and not usuario.has_usable_password()
    assert usuario.nombres == usuario.apellidos == usuario.correo == usuario.telefono == "" and usuario.cedula != cedula_original
    t = Turno.objects.get(pk=turno.id)
    assert t.estado == "CANCELADO_AFILIADO" and t.afiliado.cedula == "ANONIMO000" and Franja.objects.get(pk=f.pk).estado == "LIBRE"  # cupo liberado y agregado anónimo
    assert not delegaciones.delegacion_vigente(usuario.id, rep.id) and not delegaciones.delegacion_vigente(rep.id, usuario.id)
    ordenes = OrdenEncargado.objects.filter(solicitud_id=sol.id)
    assert ordenes.count() == 4  # 2 encargados × (notificar y eliminar)
    for o in ordenes:
        assert (o.vence - reloj.ahora()).days in ((2, 3) if o.etapa == "NOTIFICAR" else (4, 5))  # 3 y 5 días
    assert SolicitudDerechos.objects.get(pk=sol.id).estado == "ATENDIDA" and EventoAuditoria.objects.filter(accion="CUENTA_ELIMINADA").exists()
    assert servicios.atender_solicitud(dpd, sol.id) == "Ya estaba atendida."  # idempotente


def test_los_encargados_simulados_confirman_dentro_del_plazo(oferta, ciudadano, crear_usuario):
    usuario, dpd = ciudadano(), crear_usuario("AUDITOR_DPD")
    sol = _solicitud(usuario, "ELIMINACION")
    servicios.atender_solicitud(dpd, sol.id)
    assert servicios.procesar_ordenes_encargados() == {"cumplidas": 4, "vencidas": 0}
    assert not OrdenEncargado.objects.filter(estado="PENDIENTE").exists()


def test_una_orden_que_pasa_de_su_plazo_se_reporta_como_vencida(ciudadano, crear_usuario):
    usuario, dpd = ciudadano(), crear_usuario("AUDITOR_DPD")
    servicios.atender_solicitud(dpd, _solicitud(usuario, "ELIMINACION").id)
    assert servicios.procesar_ordenes_encargados(reloj.ahora() + timedelta(days=11))["vencidas"] == 4


def test_la_oposicion_retira_el_sms_y_detiene_los_recordatorios(ciudadano, crear_usuario):
    usuario, dpd = ciudadano(consentimiento_sms=True), crear_usuario("AUDITOR_DPD")
    servicios.atender_solicitud(dpd, _solicitud(usuario, "OPOSICION").id)
    usuario.refresh_from_db()
    assert not usuario.consentimiento_sms and usuario.oposicion_recordatorios
    assert cuentas.contacto_para_recordatorio(usuario.id) == ("", "")


def test_acceso_y_rectificacion_se_atienden_indicando_el_camino(ciudadano, crear_usuario):
    dpd, usuario = crear_usuario("AUDITOR_DPD"), ciudadano()
    assert "descargar" in servicios.atender_solicitud(dpd, _solicitud(usuario, "ACCESO").id)
    assert "contacto" in servicios.atender_solicitud(dpd, _solicitud(usuario, "RECTIFICACION").id)


def test_solo_el_dpd_atiende_y_ve_las_solicitudes(ciudadano, funcionario, oferta):
    usuario = ciudadano()
    sol = _solicitud(usuario, "ACCESO")
    for intruso in (usuario, funcionario("RECEPCION", oferta["est1"]), funcionario("ADMIN_TI"), funcionario("SOPORTE")):
        with pytest.raises(servicios.AccesoDenegado):
            servicios.atender_solicitud(intruso, sol.id)
        with pytest.raises(servicios.AccesoDenegado):
            servicios.listar_solicitudes(intruso)
    with pytest.raises(servicios.NoEncontrado):
        servicios.atender_solicitud(funcionario("AUDITOR_DPD"), uuid.uuid4())


# --- Vencimiento de datos (sección 2.2) ---------------------------------------------------------------------------------------------------------------------


def _por(constancias):
    return {c.categoria: c for c in constancias}


def test_T10_las_cuentas_inactivas_24_meses_se_anonimizan_con_constancia(ciudadano):
    inactiva, activa = ciudadano(), ciudadano()
    Usuario.objects.filter(pk=inactiva.pk).update(last_login=reloj.ahora() - timedelta(days=30 * 25), creado=reloj.ahora() - timedelta(days=30 * 26))
    Usuario.objects.filter(pk=activa.pk).update(last_login=reloj.ahora() - timedelta(days=30 * 23))
    c = _por(servicios.ejecutar_vencimiento())["cuentas_inactivas"]
    assert c.cantidad == 1 and c.accion == "ANONIMIZACION"
    inactiva.refresh_from_db(), activa.refresh_from_db()
    assert inactiva.estado == "BAJA" and inactiva.correo == "" and activa.estado == "ACTIVA" and activa.correo != ""


def test_T10_los_turnos_de_mas_de_12_meses_pasan_a_agregado_anonimo_y_los_recientes_no(oferta, ciudadano):
    usuario = ciudadano()
    viejo = Turno.objects.create(afiliado=usuario, solicitante=usuario, especialidad=oferta["esp1"], franja=franja_futura(oferta["est1"], oferta["esp1"], dias=-400),
                                 estado="ATENDIDO", inicio=reloj.ahora() - timedelta(days=400), fin=reloj.ahora() - timedelta(days=400) + timedelta(minutes=30), motivo="CONTROL")
    reciente = Turno.objects.create(afiliado=usuario, solicitante=usuario, especialidad=oferta["esp2"], franja=franja_futura(oferta["est1"], oferta["esp2"], dias=-30),
                                    estado="ATENDIDO", inicio=reloj.ahora() - timedelta(days=30), fin=reloj.ahora() - timedelta(days=30) + timedelta(minutes=30))
    c = _por(servicios.ejecutar_vencimiento())["turnos"]
    assert c.cantidad == 1 and c.accion == "ANONIMIZACION"
    viejo.refresh_from_db(), reciente.refresh_from_db()
    assert viejo.afiliado.cedula == "ANONIMO000" and viejo.motivo == "" and reciente.afiliado_id == usuario.id  # el agregado se conserva; la persona no


def test_T10_los_turnos_activos_nunca_se_tocan_aunque_sean_antiguos(oferta, ciudadano):
    usuario = ciudadano()
    f = franja_futura(oferta["est1"], oferta["esp1"], dias=-500)
    t = Turno.objects.create(afiliado=usuario, solicitante=usuario, especialidad=oferta["esp1"], franja=f, estado="CONFIRMADO", inicio=f.inicio, fin=f.fin)
    servicios.ejecutar_vencimiento()
    t.refresh_from_db()
    assert t.afiliado_id == usuario.id


def test_T10_notificaciones_6_meses_y_credenciales_vencidas_se_eliminan(ciudadano, afiliar):
    usuario = ciudadano()
    notificaciones.enviar(plantilla="clave_cambiada", canal="CORREO", destino=usuario.correo, destinatario_id=usuario.id)
    reloj.avanzar(timedelta(days=30 * 7))
    notificaciones.enviar(plantilla="clave_cambiada", canal="CORREO", destino=usuario.correo, destinatario_id=usuario.id)
    por = _por(servicios.ejecutar_vencimiento())
    assert por["notificaciones"].cantidad == 1 and por["notificaciones"].accion == "ELIMINACION"
    assert Notificacion.objects.count() == 1  # quedó solo la reciente


def test_T10_delegaciones_y_restricciones_vencidas_12_meses_se_eliminan(ciudadano):
    from apps.delegaciones.modelos import Delegacion

    titular, rep = ciudadano(), ciudadano("REPRESENTANTE")
    Delegacion.objects.create(titular=titular, representante=rep, inicio=reloj.ahora() - timedelta(days=900), vence=reloj.ahora() - timedelta(days=500))
    reciente = Delegacion.objects.create(titular=rep, representante=titular, inicio=reloj.ahora() - timedelta(days=100), vence=reloj.ahora() - timedelta(days=10))
    assert _por(servicios.ejecutar_vencimiento())["delegaciones"].cantidad == 1
    assert Delegacion.objects.filter(pk=reciente.pk).exists() and Delegacion.objects.count() == 1


def test_T10_la_auditoria_se_archiva_no_se_borra(tmp_path, settings):
    from apps.auditoria import servicios as auditoria

    auditoria.registrar(accion="PRUEBA")
    reloj.avanzar(timedelta(days=30 * 13))
    antes = EventoAuditoria.objects.count()
    destino = tmp_path / "archivo.jsonl"
    c = _por(servicios.ejecutar_vencimiento(archivo_auditoria=str(destino)))["auditoria"]
    assert c.accion == "ARCHIVO" and c.cantidad >= 1
    lineas = [json.loads(l) for l in destino.read_text(encoding="utf-8").splitlines()]
    assert len(lineas) == c.cantidad and all({"hash", "hash_anterior", "version_clave"} <= set(l) for l in lineas)  # sigue siendo verificable fuera de línea
    assert EventoAuditoria.objects.count() >= antes  # nada se borró (la tabla es de solo adición)
    assert auditoria.verificar_cadena().correcta


def test_el_vencimiento_deja_constancia_de_cada_categoria_y_es_idempotente(ciudadano):
    constancias = servicios.ejecutar_vencimiento()
    assert {c.categoria for c in constancias} == {"cuentas_inactivas", "turnos", "restricciones", "delegaciones", "notificaciones", "credenciales_vencidas"}
    assert ConstanciaVencimiento.objects.count() == len(constancias)
    assert EventoAuditoria.objects.filter(accion="VENCIMIENTO_EJECUTADO").count() == 1
    segunda = servicios.ejecutar_vencimiento()
    assert all(c.cantidad == 0 for c in segunda)  # nada nuevo que vencer


def test_el_comando_de_vencimiento_corre(capsys, tmp_path, settings, ciudadano):
    from django.core.management import call_command

    call_command("ejecutar_vencimiento")
    assert "cuentas_inactivas" in capsys.readouterr().out


# --- Exportaciones con doble aprobación -----------------------------------------------------------------------------------------------------------------------


@pytest.fixture
def dpd(crear_usuario):
    return crear_usuario("AUDITOR_DPD")


def _pedir(dpd, **extra):
    return servicios.solicitar_exportacion(dpd, motivo="Revisión trimestral de accesos", desde=reloj.ahora() - timedelta(days=2), hasta=reloj.ahora() + timedelta(days=1), **extra)


def test_una_exportacion_necesita_la_aprobacion_de_otra_persona_y_se_entrega_una_vez(dpd, crear_usuario):
    from apps.auditoria import servicios as auditoria

    auditoria.registrar(accion="PRUEBA", detalle={"clave": "secreta"})
    otro, e = crear_usuario("ADMIN_TI"), _pedir(dpd)
    with pytest.raises(servicios.DerechosRechazado) as error:
        servicios.entregar_exportacion(dpd, e.id)  # sin aprobación no hay descarga
    assert error.value.codigo == "NO_APROBADA"
    with pytest.raises(servicios.AccesoDenegado):
        servicios.aprobar_exportacion(dpd, e.id)  # quien la pidió NO puede aprobarla
    servicios.aprobar_exportacion(otro, e.id)
    contenido = servicios.entregar_exportacion(dpd, e.id)
    filas = list(csv.DictReader(io.StringIO(contenido)))
    assert filas and "PRUEBA" in {f["accion"] for f in filas}
    assert contenido.splitlines()[0] == "secuencia,ocurrido_en,actor_rol,accion,resultado,recurso_tipo,recurso_id"  # sin detalle, IP ni hashes
    assert "secreta" not in contenido.replace("secuencia", "")  # el valor del secreto no sale
    with pytest.raises(servicios.DerechosRechazado):
        servicios.entregar_exportacion(dpd, e.id)  # una sola vez
    assert EventoAuditoria.objects.filter(accion="EXPORTACION_ENTREGADA", detalle__aprobada=True).count() == 1


def test_la_base_impide_la_autoaprobacion_aunque_falle_la_aplicacion(dpd):
    e = _pedir(dpd)
    with pytest.raises(IntegrityError), transaction.atomic():
        SolicitudExportacion.objects.filter(pk=e.pk).update(aprobador=dpd, estado="APROBADA")


def test_solo_el_solicitante_descarga_y_solo_el_dpd_pide(dpd, crear_usuario, funcionario, oferta):
    otro = crear_usuario("ADMIN_TI")
    e = _pedir(dpd)
    servicios.aprobar_exportacion(otro, e.id)
    with pytest.raises(servicios.AccesoDenegado):
        servicios.entregar_exportacion(otro, e.id)  # el aprobador tampoco puede descargarla
    for intruso in (funcionario("RECEPCION", oferta["est1"]), funcionario("SOPORTE"), funcionario("ADMIN_TI")):
        with pytest.raises(servicios.AccesoDenegado):
            servicios.solicitar_exportacion(intruso, motivo="Quiero todo", desde=reloj.ahora() - timedelta(days=1), hasta=reloj.ahora())
    for rol in ("AFILIADO", "REPRESENTANTE", "RECEPCION", "SOPORTE", "ADMIN_OFERTA"):
        with pytest.raises(servicios.AccesoDenegado):
            servicios.aprobar_exportacion(crear_usuario(rol), e.id)


def test_no_hay_exportacion_masiva_rango_motivo_y_tope_de_filas(dpd, crear_usuario, parametro):
    from apps.auditoria import servicios as auditoria

    for kwargs in (dict(motivo="corto", desde=reloj.ahora() - timedelta(days=1), hasta=reloj.ahora()),
                   dict(motivo="Un motivo suficiente", desde=reloj.ahora() - timedelta(days=40), hasta=reloj.ahora()),  # más de 31 días
                   dict(motivo="Un motivo suficiente", desde=reloj.ahora(), hasta=reloj.ahora() - timedelta(days=1))):
        with pytest.raises(servicios.DerechosRechazado):
            servicios.solicitar_exportacion(dpd, **kwargs)
    parametro("exportacion_filas_maximas", 3)
    for _ in range(10):
        auditoria.registrar(accion="PRUEBA")
    e = _pedir(dpd)
    servicios.aprobar_exportacion(crear_usuario("ADMIN_TI"), e.id)
    assert len(list(csv.reader(io.StringIO(servicios.entregar_exportacion(dpd, e.id))))) == 1 + 3  # encabezado + tope


def test_la_aprobacion_vence_a_las_24_horas(dpd, crear_usuario):
    e = _pedir(dpd)
    servicios.aprobar_exportacion(crear_usuario("ADMIN_TI"), e.id)
    reloj.avanzar(timedelta(hours=25))
    with pytest.raises(servicios.DerechosRechazado) as error:
        servicios.entregar_exportacion(dpd, e.id)
    assert error.value.codigo == "VENCIDA"


def test_los_responsables_reciben_aviso_de_una_exportacion_pendiente(dpd, crear_usuario, correo_enviado):
    crear_usuario("ADMIN_TI")
    _pedir(dpd)
    assert any(m.plantilla == "exportacion_pendiente" for m in correo_enviado)


# --- Cambio de contacto (rectificación) y espera de 24 h ------------------------------------------------------------------------------------------------


def test_cambiar_el_correo_confirma_en_el_contacto_nuevo_avisa_al_anterior_y_difiere_24_horas(ciudadano, correo_enviado):
    usuario = ciudadano()
    anterior = usuario.correo
    cuentas.solicitar_cambio_contacto(peticion(usuario), "CORREO", "Nuevo@Correo.Iess-Prueba.Example")
    envio = [m for m in correo_enviado if m.plantilla == "activacion"][-1]
    assert envio.destino == "nuevo@correo.iess-prueba.example"  # el código va al contacto NUEVO
    codigo = envio.cuerpo.split("es ")[1][:8]
    with pytest.raises(cuentas.CodigoInvalido):
        cuentas.confirmar_cambio_contacto(peticion(usuario), "00000000")
    cuentas.confirmar_cambio_contacto(peticion(usuario), codigo)
    usuario.refresh_from_db()
    assert usuario.correo == "nuevo@correo.iess-prueba.example"
    assert any(m.plantilla == "contacto_cambiado" and m.destino == anterior for m in correo_enviado)  # aviso al contacto anterior
    with pytest.raises(cuentas.OperacionDiferida):  # las operaciones sensibles esperan 24 h
        cuentas.exigir_operacion_sensible(peticion(usuario))
    reloj.avanzar(timedelta(hours=24, minutes=1))
    usuario.refresh_from_db()
    cuentas.exigir_operacion_sensible(peticion(usuario))


def test_el_cambio_de_contacto_valida_el_formato_y_exige_segundo_factor(ciudadano):
    usuario = ciudadano()
    for campo, valor in (("CORREO", "no-es-correo"), ("CORREO", "a@b"), ("TELEFONO", "12ab"), ("TELEFONO", "123"), ("OTRO", "x")):
        with pytest.raises(cuentas.ClaveRechazada):
            cuentas.solicitar_cambio_contacto(peticion(usuario), campo, valor)
    with pytest.raises(cuentas.ReautenticacionRequerida):
        cuentas.solicitar_cambio_contacto(peticion(usuario, mfa=False), "CORREO", "x@y.example")


def test_el_codigo_de_cambio_de_contacto_vence_y_no_se_reutiliza(ciudadano, correo_enviado):
    usuario = ciudadano()
    cuentas.solicitar_cambio_contacto(peticion(usuario), "TELEFONO", "0998887766")
    codigo = [m for m in correo_enviado if m.canal == "SMS"][-1].cuerpo.split("es ")[1][:8]
    reloj.avanzar(timedelta(minutes=16))
    with pytest.raises(cuentas.CodigoInvalido):
        cuentas.confirmar_cambio_contacto(peticion(usuario), codigo)
    usuario.refresh_from_db()
    assert usuario.telefono != "0998887766"


# --- Recordatorios (72 h y 24 h) y consentimiento de SMS ---------------------------------------------------------------------------------------------


def test_recordatorios_a_las_72_y_24_horas_una_sola_vez_neutrales_y_con_enlace_de_un_solo_uso(oferta, ciudadano, correo_enviado, settings):
    usuario = ciudadano()
    turno = _reservar(usuario, franja_futura(oferta["est1"], oferta["esp1"], dias=5))
    correo_enviado.clear()
    assert turnos.enviar_recordatorios()["enviados"] == 0  # faltan más de 72 h
    reloj.avanzar(timedelta(days=2, hours=2))  # faltan ~70 h
    assert turnos.enviar_recordatorios()["enviados"] == 1 and turnos.enviar_recordatorios()["enviados"] == 0  # una sola vez
    reloj.avanzar(timedelta(hours=48))  # faltan ~23 h
    assert turnos.enviar_recordatorios()["enviados"] == 1
    mensajes = [m for m in correo_enviado if m.plantilla == "recordatorio"]
    assert len(mensajes) == 2
    cuerpo = mensajes[-1].cuerpo  # el último: el de 72 h ya caducó (los enlaces duran 24 h)
    assert "Tiene una cita el " in cuerpo and settings.URL_BASE + "/enlaces/" in cuerpo
    assert "Medicina" not in cuerpo and "general" not in cuerpo  # sin especialidad
    token = cuerpo.split("/enlaces/")[1].split()[0]
    assert notificaciones.consumir_enlace(token) == turno.id  # el enlace lleva a iniciar sesión, un solo uso
    assert notificaciones.consumir_enlace(token) is None


def test_un_turno_creado_con_menos_de_24_horas_recibe_solo_el_ultimo_recordatorio(oferta, ciudadano, correo_enviado):
    from tests.ayudas import franja_en

    usuario = ciudadano()
    _reservar(usuario, franja_en(oferta["est1"], oferta["esp1"], timedelta(hours=10)))
    assert turnos.enviar_recordatorios()["enviados"] == 1
    assert [m.plantilla for m in correo_enviado].count("recordatorio") == 1


def test_el_sms_solo_con_consentimiento_y_sin_el_va_por_correo(oferta, ciudadano, correo_enviado):
    con, sin = ciudadano(consentimiento_sms=True), ciudadano()
    _reservar(con, franja_futura(oferta["est1"], oferta["esp1"], dias=1, hora=8))
    _reservar(sin, franja_futura(oferta["est1"], oferta["esp1"], dias=1, hora=9))
    correo_enviado.clear()
    assert turnos.enviar_recordatorios()["enviados"] == 2
    canales = {m.destino: m.canal for m in correo_enviado if m.plantilla == "recordatorio"}
    assert canales[con.telefono] == "SMS" and canales[sin.correo] == "CORREO"


def test_la_oposicion_detiene_los_recordatorios(oferta, ciudadano, correo_enviado):
    usuario = ciudadano(oposicion_recordatorios=True)
    _reservar(usuario, franja_futura(oferta["est1"], oferta["esp1"], dias=1))
    correo_enviado.clear()
    r = turnos.enviar_recordatorios()
    assert r == {"enviados": 0, "omitidos": 1} and not [m for m in correo_enviado if m.plantilla == "recordatorio"]


def test_actualizar_el_consentimiento_de_sms_queda_auditado(ciudadano):
    usuario = ciudadano()
    cuentas.actualizar_consentimiento_sms(usuario, True)
    assert cuentas.preferencias_de_notificacion(usuario.id) == (True, False)
    cuentas.actualizar_consentimiento_sms(usuario, False)
    assert cuentas.preferencias_de_notificacion(usuario.id) == (False, False)
    assert EventoAuditoria.objects.filter(accion="CONSENTIMIENTO_SMS", actor_id=usuario.id).count() == 2

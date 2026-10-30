"""Reglas D01-D18 del Anexo I: cada una con un caso que la dispara y otro que no, y su acción automática (T12, R23)."""
import uuid
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection

from apps.auditoria import servicios as auditoria
from apps.auditoria.servicios import Acciones
from apps.cuentas import servicios as cuentas
from apps.deteccion import servicios
from apps.deteccion.modelos import Alerta
from apps.notificaciones import servicios as notificaciones
from config import bloqueos, metricas, reloj
from tests.ayudas import franja_futura

pytestmark = pytest.mark.django_db
IP = "198.51.100.10"


@pytest.fixture(autouse=True)
def respuestas_limpias():
    bloqueos.desbloquear_origen(IP)
    bloqueos.desactivar_desafio()
    bloqueos.desactivar_sala_espera()
    yield
    bloqueos.desbloquear_origen(IP)
    bloqueos.desactivar_desafio()
    bloqueos.desactivar_sala_espera()


def _eventos(accion, n, *, ip=IP, actor_id=None, detalle=None, resultado="OK", distinto=None, recurso=None):
    for i in range(n):
        d = dict(detalle or {})
        if distinto:
            d[distinto] = f"ref-{i:04d}"
        auditoria.registrar(accion=accion, actor_id=actor_id, ip=ip, detalle=d, resultado=resultado, recurso_tipo="x", recurso_id=(f"{recurso}{i}" if recurso else ""))


def _regla(id_):
    return servicios.evaluar(reglas={id_})


# --- D01 / D02 / D03 / D04: identidad ------------------------------------------------------------------------------------------------------------------


def test_D01_cincuenta_fallos_desde_una_ip_alertan_y_bloquean_el_origen():
    _eventos(Acciones.LOGIN_FALLIDO, 49, resultado="DENEGADO")
    assert _regla("D01") == [] and bloqueos.origen_bloqueado(IP) == 0
    _eventos(Acciones.LOGIN_FALLIDO, 1, resultado="DENEGADO")
    (a,) = _regla("D01")
    assert a.severidad == "ALTA" and a.detalle["fallos"] == 50 and a.detalle["origen"] == "198.51.x.x"  # nunca la IP completa
    assert bloqueos.origen_bloqueado(IP) > 25 * 60 and "Bloqueo temporal" in a.accion_automatica
    assert _regla("D01") == []  # el mismo hecho no genera otra alerta ni repite la acción


def test_D01_las_ip_distintas_no_se_suman():
    for i in range(50):
        _eventos(Acciones.LOGIN_FALLIDO, 1, ip=f"198.51.100.{i + 20}", resultado="DENEGADO")
    assert _regla("D01") == []


def test_D02_fallos_en_200_cuentas_distintas_activan_el_desafio_adaptativo():
    _eventos(Acciones.LOGIN_FALLIDO, 199, detalle={}, distinto="cedula_ref", resultado="DENEGADO")
    assert _regla("D02") == [] and not bloqueos.desafio_activo()
    _eventos(Acciones.LOGIN_FALLIDO, 1, detalle={"cedula_ref": "ref-extra"}, resultado="DENEGADO")
    (a,) = _regla("D02")
    assert a.severidad == "ALTA" and bloqueos.desafio_activo()


def test_D02_muchos_fallos_en_pocas_cuentas_no_es_relleno_distribuido():
    _eventos(Acciones.LOGIN_FALLIDO, 300, detalle={"cedula_ref": "una-sola"}, resultado="DENEGADO")
    assert _regla("D02") == []


def test_D03_una_cuenta_atacada_desde_5_ips_pierde_la_clave_y_se_avisa_al_titular(crear_usuario, correo_enviado):
    usuario = crear_usuario()
    ref = cuentas._ref_cedula(usuario.cedula)
    for i in range(5):
        _eventos(Acciones.LOGIN_FALLIDO, 2, ip=f"198.51.100.{30 + i}", detalle={"cedula_ref": ref}, resultado="DENEGADO")
    (a,) = _regla("D03")
    usuario.refresh_from_db()
    assert a.severidad == "ALTA" and not usuario.has_usable_password()  # debe restablecerla por el flujo normal
    assert any(m.plantilla == "alerta_seguridad" and m.destino == usuario.correo for m in correo_enviado)
    assert cuentas.solicitar_recuperacion(usuario.cedula) is None  # y puede: la recuperación sigue funcionando


def test_D03_los_fallos_desde_pocas_ip_no_disparan():
    for i in range(4):
        _eventos(Acciones.LOGIN_FALLIDO, 3, ip=f"198.51.100.{40 + i}", detalle={"cedula_ref": "r1"}, resultado="DENEGADO")
    assert _regla("D03") == []


def test_D04_enumeracion_de_30_cedulas_desde_una_ip_bloquea_el_origen():
    _eventos(Acciones.ACTIVACION_SOLICITADA, 29, distinto="cedula_ref", resultado="DENEGADO")
    assert _regla("D04") == []
    _eventos(Acciones.RECUPERACION_SOLICITADA, 1, detalle={"cedula_ref": "ref-otra"}, resultado="DENEGADO")
    (a,) = _regla("D04")
    assert a.severidad == "MEDIA" and bloqueos.origen_bloqueado(IP) > 0


# --- D05 a D08: abuso y uso interno ----------------------------------------------------------------------------------------------------------------------


def test_D05_primer_inicio_desde_un_dispositivo_nuevo_es_informativo():
    _eventos(Acciones.DISPOSITIVO_NUEVO, 1)
    (a,) = _regla("D05")
    assert a.severidad == "INFORMATIVA" and a.accion_automatica == ""


def test_D06_mas_de_10_intentos_de_reserva_en_una_hora_por_cuenta():
    actor = uuid.uuid4()
    _eventos(Acciones.RESERVA_RECHAZADA, 10, actor_id=actor, resultado="DENEGADO")
    assert _regla("D06") == []
    _eventos(Acciones.RESERVA_RECHAZADA, 1, actor_id=actor, resultado="DENEGADO")
    (a,) = _regla("D06")
    assert a.severidad == "MEDIA" and a.detalle["intentos"] == 11


def test_D06_cinco_cuentas_reservando_desde_una_ip_limitan_el_origen():
    for _ in range(5):
        _eventos(Acciones.RESERVA_CREADA, 1, actor_id=uuid.uuid4())
    alertas = _regla("D06")
    assert len(alertas) == 1 and bloqueos.origen_bloqueado(IP) > 0
    bloqueos.desbloquear_origen(IP)


def test_D06_cuatro_cuentas_en_una_ip_es_normal_por_ejemplo_una_familia():
    for _ in range(4):
        _eventos(Acciones.RESERVA_CREADA, 1, actor_id=uuid.uuid4())
    assert _regla("D06") == []


def test_D07_veinte_respuestas_403_o_404_a_un_usuario_en_5_minutos():
    actor = uuid.uuid4()
    _eventos(Acciones.ACCESO_DENEGADO, 19, actor_id=actor, resultado="DENEGADO")
    assert _regla("D07") == []
    _eventos(Acciones.ACCESO_DENEGADO, 1, actor_id=actor, resultado="DENEGADO")
    assert [a.regla for a in _regla("D07")] == ["D07"]


def test_D07_los_sondeos_reales_de_ids_ajenos_alimentan_la_regla(oferta, ciudadano):
    """De punta a punta: pedir 20 turnos inexistentes (404) deja 20 eventos y la regla lo detecta."""
    from apps.turnos import servicios as turnos

    usuario = ciudadano()
    for _ in range(20):
        with pytest.raises(turnos.NoEncontrado):
            turnos.detalle_turno(usuario, uuid.uuid4())
    assert [a.regla for a in _regla("D07")] == ["D07"]


def _a_hora_local(hora: int):
    from django.utils import timezone as zona

    local = zona.localtime(reloj.ahora()).replace(hour=hora, minute=0, second=0, microsecond=0)
    reloj.avanzar(local - zona.localtime(reloj.ahora()))


def test_D08_cien_fichas_distintas_en_una_hora_por_un_funcionario():
    _a_hora_local(10)
    actor = uuid.uuid4()
    _eventos(Acciones.FICHA_CONSULTADA, 99, actor_id=actor, recurso="turno-")
    assert _regla("D08") == []
    _eventos(Acciones.FICHA_CONSULTADA, 1, actor_id=actor, recurso="turno-extra-")
    (a,) = _regla("D08")
    assert a.severidad == "ALTA" and a.detalle["fichas"] == 100


def test_D08_consultas_fuera_del_horario_laboral_alertan_y_dentro_no():
    actor = uuid.uuid4()
    _a_hora_local(10)
    _eventos(Acciones.CONSULTA_CONTACTO, 3, actor_id=actor, recurso="a")
    assert _regla("D08") == []
    _a_hora_local(23)
    _eventos(Acciones.CONSULTA_CONTACTO, 1, actor_id=actor, recurso="b")
    (a,) = _regla("D08")
    assert "fuera del horario" in a.titulo


# --- D09 a D11: exportaciones, emergencia e integridad -------------------------------------------------------------------------------------------------


def test_D09_toda_exportacion_avisa_y_una_sin_aprobacion_es_critica():
    _eventos(Acciones.EXPORTACION_ENTREGADA, 1, detalle={"aprobada": True})
    _eventos(Acciones.EXPORTACION_ENTREGADA, 1, detalle={"aprobada": False})
    por_severidad = sorted(a.severidad for a in _regla("D09"))
    assert por_severidad == ["CRITICA", "MEDIA"]


def test_D10_el_acceso_de_emergencia_alerta_con_plazo_de_revision_y_queda_abierta(crear_usuario, correo_enviado):
    crear_usuario("AUDITOR_DPD")
    _eventos(Acciones.ACCESO_EMERGENCIA, 1, actor_id=uuid.uuid4())
    (a,) = _regla("D10")
    assert a.severidad == "ALTA" and a.estado == "ABIERTA" and a.vence_en - reloj.ahora() > timedelta(days=6)
    assert correo_enviado  # aviso al responsable


def test_D11_una_alteracion_de_la_cadena_es_critica_de_punta_a_punta():
    auditoria.registrar(accion="PRUEBA")
    auditoria.registrar(accion="PRUEBA")
    with connection.cursor() as c:  # un atacante con acceso a la base desactiva la protección y edita
        c.execute("ALTER TABLE auditoria_evento DISABLE TRIGGER auditoria_sin_update_delete")
        c.execute("UPDATE auditoria_evento SET resultado = 'DENEGADO' WHERE accion = 'PRUEBA' AND secuencia = (SELECT min(secuencia) FROM auditoria_evento WHERE accion = 'PRUEBA')")
        c.execute("ALTER TABLE auditoria_evento ENABLE TRIGGER auditoria_sin_update_delete")
    with pytest.raises(CommandError):
        call_command("verificar_auditoria")
    (a,) = _regla("D11")
    assert a.severidad == "CRITICA"


# --- D12 a D14: disponibilidad -------------------------------------------------------------------------------------------------------------------------------


def test_D12_mas_de_2_por_ciento_de_errores_5xx_en_5_minutos():
    for _ in range(100):
        metricas.registrar(0.05, 200)
    for _ in range(1):
        metricas.registrar(0.05, 500)
    assert _regla("D12") == []  # 1 %: normal
    for _ in range(4):
        metricas.registrar(0.05, 503)
    (a,) = _regla("D12")
    assert a.severidad == "ALTA" and a.detalle["porcentaje_5xx"] > 2


def test_D12_una_muestra_pequena_no_dispara():
    for _ in range(10):
        metricas.registrar(0.05, 500)
    assert _regla("D12") == []


def test_D13_p95_alto_activa_la_sala_de_espera():
    for _ in range(100):
        metricas.registrar(0.1, 200)
    assert _regla("D13") == [] and not bloqueos.sala_espera_activa()
    for _ in range(100):
        metricas.registrar(4.0, 200)
    (a,) = _regla("D13")
    assert a.severidad == "MEDIA" and bloqueos.sala_espera_activa()


def test_las_metricas_calculan_p95_y_porcentajes():
    for _ in range(95):
        metricas.registrar(0.04, 200)
    for _ in range(5):
        metricas.registrar(1.5, 500)
    r = metricas.resumen(300)
    assert r["total"] == 100 and r["errores_5xx"] == 5 and r["porcentaje_5xx"] == 5.0 and r["p95"] <= 0.05


def test_D14_cinco_fallos_de_terceros_en_30_segundos():
    _eventos(Acciones.AGENDA_NO_DISPONIBLE, 4, resultado="ERROR")
    assert _regla("D14") == []
    _eventos(Acciones.AFILIACION_NO_DISPONIBLE, 1, resultado="ERROR")
    (a,) = _regla("D14")
    assert a.severidad == "MEDIA" and "PENDIENTE" in a.accion_automatica


# --- D15 a D18: operación, respaldos y tokens ------------------------------------------------------------------------------------------------------------


def test_D15_un_respaldo_fallido_alerta():
    _eventos(Acciones.RESPALDO_FALLIDO, 1, resultado="ERROR")
    assert [a.regla for a in _regla("D15")] == ["D15"]


def test_D15_sin_prueba_de_restauracion_en_90_dias_alerta_y_con_prueba_reciente_no():
    _eventos(Acciones.RESPALDO_REALIZADO, 1)
    reloj.avanzar(timedelta(days=89))
    assert _regla("D15") == []
    reloj.avanzar(timedelta(days=2))
    (a,) = _regla("D15")
    assert "restauración" in a.titulo
    # Con una prueba reciente el hallazgo desaparece para la siguiente ventana.
    _eventos(Acciones.RESTAURACION_PROBADA, 1)
    reloj.avanzar(timedelta(days=1))
    assert _regla("D15") == []


def test_D16_un_cve_critico_o_kev_abre_un_ticket_con_plazo_de_72_horas():
    _eventos(Acciones.VULNERABILIDAD_DETECTADA, 1, detalle={"severidad": "BAJA", "paquete": "x"})
    assert _regla("D16") == []
    _eventos(Acciones.VULNERABILIDAD_DETECTADA, 1, detalle={"severidad": "KEV", "paquete": "django"})
    (a,) = _regla("D16")
    assert a.severidad == "ALTA" and timedelta(hours=71) < a.vence_en - reloj.ahora() <= timedelta(hours=72)


def test_D17_el_mismo_token_desde_dos_ips_se_invalida(oferta, ciudadano):
    from apps.notificaciones.modelos import TokenEnlace
    from apps.turnos import servicios as turnos

    usuario = ciudadano()
    turno, _ = turnos.reservar(solicitante=usuario, franja_id=franja_futura(oferta["est1"], oferta["esp1"]).id, motivo="", clave_idempotencia=uuid.uuid4().hex)
    token = notificaciones.crear_enlace(destinatario_id=usuario.id, turno_id=turno.id)
    ref = TokenEnlace.objects.get().token_hash[:12]
    _eventos(Acciones.ENLACE_RECHAZADO, 1, ip="198.51.100.50", detalle={"token_ref": ref}, resultado="DENEGADO")
    assert _regla("D17") == []  # un solo origen: nada
    _eventos(Acciones.ENLACE_RECHAZADO, 1, ip="198.51.100.51", detalle={"token_ref": ref}, resultado="DENEGADO")
    (a,) = _regla("D17")
    assert a.severidad == "MEDIA" and notificaciones.consumir_enlace(token) is None  # el token ya no sirve


def test_D17_un_enlace_real_usado_y_reintentado_desde_otra_ip_dispara_la_regla(oferta, ciudadano):
    from config import contexto
    from apps.turnos import servicios as turnos

    usuario = ciudadano()
    turno, _ = turnos.reservar(solicitante=usuario, franja_id=franja_futura(oferta["est1"], oferta["esp1"]).id, motivo="", clave_idempotencia=uuid.uuid4().hex)
    token = notificaciones.crear_enlace(destinatario_id=usuario.id, turno_id=turno.id)
    assert notificaciones.consumir_enlace(token) == turno.id  # primer uso desde la IP del contexto de prueba
    contexto.fijar(ip="198.51.100.77")
    assert notificaciones.consumir_enlace(token) is None  # reintento desde otro origen
    assert [a.regla for a in _regla("D17")] == ["D17"]


def test_D18_el_alta_de_un_funcionario_se_detecta():
    cuentas.aprovisionar_cuenta(cedula="1750000026", rol="SOPORTE", nombres="Nuevo", apellidos="Funcionario", correo="n@correo.iess-prueba.example", telefono="0990000099", clave="Clave-Larga-Segura-91")
    (a,) = _regla("D18")
    assert a.severidad == "MEDIA" and a.detalle["rol_nuevo"] == "SOPORTE"


# --- Comportamiento general del módulo ----------------------------------------------------------------------------------------------------------------------


def test_una_regla_que_falla_no_detiene_a_las_demas(monkeypatch):
    monkeypatch.setitem(servicios.REGLAS, "D05", lambda ahora: (_ for _ in ()).throw(RuntimeError("falla simulada")))
    _eventos(Acciones.RESPALDO_FALLIDO, 1, resultado="ERROR")
    assert [a.regla for a in servicios.evaluar()] == ["D15"]


def test_las_alertas_no_llevan_datos_personales():
    _eventos(Acciones.LOGIN_FALLIDO, 50, resultado="DENEGADO", detalle={"cedula_ref": "abc123"})
    _regla("D01")
    volcado = str([(a.titulo, a.detalle, a.accion_automatica) for a in Alerta.objects.all()])
    assert IP not in volcado and "1750" not in volcado


def test_cada_alerta_y_cada_accion_automatica_quedan_en_la_auditoria():
    _eventos(Acciones.LOGIN_FALLIDO, 50, resultado="DENEGADO")
    (a,) = _regla("D01")
    from apps.auditoria.modelos import EventoAuditoria

    assert EventoAuditoria.objects.filter(accion="ALERTA_GENERADA", recurso_id=str(a.id)).exists()
    assert EventoAuditoria.objects.filter(accion="ACCION_AUTOMATICA", recurso_id=str(a.id)).exists()


def test_existen_las_18_reglas_con_umbrales_parametrizables(parametro):
    assert sorted(servicios.REGLAS) == [f"D{n:02d}" for n in range(1, 19)]
    parametro("d01_fallos_por_ip", 3)  # el umbral sale de la configuración, no del código
    _eventos(Acciones.LOGIN_FALLIDO, 3, resultado="DENEGADO")
    assert len(_regla("D01")) == 1


def test_el_comando_evaluar_deteccion_muestra_las_alertas(capsys):
    _eventos(Acciones.RESPALDO_FALLIDO, 1, resultado="ERROR")
    call_command("evaluar_deteccion")
    salida = capsys.readouterr().out
    assert "D15" in salida and "1 alerta(s) nueva(s)" in salida


def test_el_panel_solo_lo_ven_auditor_y_ti_y_reconocer_es_idempotente(crear_usuario):
    _eventos(Acciones.RESPALDO_FALLIDO, 1, resultado="ERROR")
    (a,) = _regla("D15")
    for rol in ("AFILIADO", "REPRESENTANTE", "RECEPCION", "PROFESIONAL", "SOPORTE", "ADMIN_OFERTA"):
        with pytest.raises(servicios.AccesoDenegado):
            servicios.listar_alertas(crear_usuario(rol))
    for rol in ("AUDITOR_DPD", "ADMIN_TI"):
        assert servicios.listar_alertas(crear_usuario(rol)).total == 1
    ti = crear_usuario("ADMIN_TI")
    assert servicios.reconocer_alerta(ti, a.id, "Revisado").estado == "RECONOCIDA"
    assert servicios.reconocer_alerta(ti, a.id, "Otra nota").nota == "Revisado"  # idempotente
    assert servicios.listar_alertas(ti).abiertas_por_severidad["ALTA"] == 0
    with pytest.raises(servicios.NoEncontrado):
        servicios.reconocer_alerta(ti, uuid.uuid4())
    with pytest.raises(servicios.NoEncontrado):
        servicios.reconocer_alerta(ti, "no-es-uuid")

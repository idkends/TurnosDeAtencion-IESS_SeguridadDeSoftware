"""Sesiones: 24 h totales, máximo 3 dispositivos, listar/revocar, cerrar todas al cambiar la clave, aviso por dispositivo nuevo."""
from datetime import timedelta

import pytest
from django.contrib.sessions.models import Session
from django.test import Client
from django.urls import reverse

from apps.cuentas import servicios
from apps.cuentas.modelos import SesionDispositivo
from config import reloj

pytestmark = pytest.mark.django_db


def _login(crear_request, usuario, clave, dispositivo=None):
    cookies = {"__Host-dispositivo": dispositivo} if dispositivo else {}
    request = crear_request(cookies=cookies)
    resultado = servicios.autenticar(request, usuario.cedula, clave)
    return request, resultado


def _dispositivo(n):
    return f"dispositivo-prueba-numero-{n:04d}-xxxx"  # 16-64 caracteres alfanuméricos


def test_la_sesion_dura_24_horas_absolutas_y_no_se_renueva_con_la_actividad(crear_usuario, crear_request, clave_buena):
    usuario = crear_usuario()
    request, _ = _login(crear_request, usuario, clave_buena)
    fila = Session.objects.get(session_key=request.session.session_key)
    esperado = reloj.ahora() + timedelta(hours=24)
    assert abs((fila.expire_date - esperado).total_seconds()) < 5
    # La actividad posterior guarda la sesión, pero NO mueve el vencimiento.
    reloj.avanzar(timedelta(hours=3))
    servicios.registrar_actividad(request)
    request.session.save()
    fila.refresh_from_db()
    assert abs((fila.expire_date - esperado).total_seconds()) < 5


def test_T03_sesion_vencida_a_las_24_horas_es_rechazada_por_el_servidor(crear_usuario, clave_buena):
    usuario = crear_usuario()
    cliente = Client()
    cliente.post(reverse("cuentas:ingresar"), {"cedula": usuario.cedula, "clave": clave_buena})
    assert cliente.get(reverse("cuentas:sesiones")).status_code == 200  # con sesión válida entra
    Session.objects.update(expire_date=reloj.ahora() - timedelta(seconds=1))  # vence en la base
    respuesta = cliente.get(reverse("cuentas:sesiones"))
    assert respuesta.status_code == 302 and "/sesion" in respuesta["Location"]


def test_T03_el_middleware_cierra_la_sesion_pasadas_24_horas_aunque_la_base_no_la_haya_borrado(crear_usuario, clave_buena):
    usuario = crear_usuario()
    cliente = Client()
    cliente.post(reverse("cuentas:ingresar"), {"cedula": usuario.cedula, "clave": clave_buena})
    reloj.avanzar(timedelta(hours=24, minutes=1))
    Session.objects.update(expire_date=reloj.ahora() + timedelta(days=1))  # simula un vencimiento de base mal calculado
    respuesta = cliente.get(reverse("cuentas:sesiones"))
    assert respuesta.status_code == 302  # el middleware aplica su propio tope de 24 h


def test_maximo_3_dispositivos_el_cuarto_cierra_el_mas_antiguo(crear_usuario, crear_request, clave_buena, correo_enviado):
    usuario = crear_usuario()
    sesiones = []
    for n in range(1, 5):
        request, _ = _login(crear_request, usuario, clave_buena, _dispositivo(n))
        sesiones.append(request.session.session_key)
        reloj.avanzar(timedelta(minutes=1))
    activas = SesionDispositivo.objects.filter(usuario=usuario, revocada_en__isnull=True)
    assert activas.count() == 3
    assert not Session.objects.filter(session_key=sesiones[0]).exists()  # la más antigua se cerró de verdad
    assert Session.objects.filter(session_key__in=sesiones[1:]).count() == 3
    assert SesionDispositivo.objects.get(session_key=sesiones[0]).motivo_revocacion == "LIMITE_DISPOSITIVOS"
    assert any(m.plantilla == "sesion_revocada" for m in correo_enviado)


def test_el_mismo_dispositivo_reemplaza_su_sesion_en_lugar_de_ocupar_otro_cupo(crear_usuario, crear_request, clave_buena):
    usuario = crear_usuario()
    primera, _ = _login(crear_request, usuario, clave_buena, _dispositivo(1))
    segunda, _ = _login(crear_request, usuario, clave_buena, _dispositivo(1))
    assert SesionDispositivo.objects.filter(usuario=usuario, revocada_en__isnull=True).count() == 1
    assert not Session.objects.filter(session_key=primera.session.session_key).exists()


def test_listar_y_revocar_sesiones_propias(crear_usuario, crear_request, clave_buena):
    usuario = crear_usuario()
    r1, _ = _login(crear_request, usuario, clave_buena, _dispositivo(1))
    r2, _ = _login(crear_request, usuario, clave_buena, _dispositivo(2))
    vistas = servicios.listar_sesiones(r1)
    assert len(vistas) == 2 and sum(v.actual for v in vistas) == 1
    ajena = next(v for v in vistas if not v.actual)
    assert servicios.revocar_sesion(r1, ajena.id) is True
    assert not Session.objects.filter(session_key=r2.session.session_key).exists()  # la otra sesión quedó cerrada


def test_CA07_no_se_puede_revocar_la_sesion_de_otra_persona_y_la_respuesta_no_distingue(crear_usuario, crear_request, clave_buena):
    dueña, otra = crear_usuario(), crear_usuario()
    rd, _ = _login(crear_request, dueña, clave_buena)
    ro, _ = _login(crear_request, otra, clave_buena)
    sesion_de_otra = SesionDispositivo.objects.get(session_key=ro.session.session_key)
    existe_ajena = servicios.revocar_sesion(rd, sesion_de_otra.id)
    no_existe = servicios.revocar_sesion(rd, "00000000-0000-0000-0000-000000000000")
    basura = servicios.revocar_sesion(rd, "no-es-un-uuid")
    assert existe_ajena is False and no_existe is False and basura is False  # mismo resultado
    assert Session.objects.filter(session_key=ro.session.session_key).exists()  # la ajena sigue viva


def test_al_cambiar_la_clave_se_cierran_todas_las_sesiones(crear_usuario, crear_request, clave_buena, codigo_totp, correo_enviado):
    import pyotp

    usuario = crear_usuario()
    otra, _ = _login(crear_request, usuario, clave_buena, _dispositivo(2))
    actual, _ = _login(crear_request, usuario, clave_buena, _dispositivo(1))
    # El cambio de clave es una operación sensible: se configura TOTP y se confirma.
    conf = servicios.obtener_configuracion_mfa(actual)
    servicios.confirmar_configuracion_mfa(actual, codigo_totp(conf.secreto))
    reloj.avanzar(timedelta(seconds=1))
    servicios.cambiar_clave(actual, clave_buena, "Nueva-Clave-Larga-9182")
    assert not Session.objects.filter(session_key__in=[otra.session.session_key]).exists()
    assert SesionDispositivo.objects.filter(usuario=usuario, revocada_en__isnull=True).count() == 0
    usuario.refresh_from_db()
    assert usuario.check_password("Nueva-Clave-Larga-9182") and not usuario.check_password(clave_buena)
    assert any(m.plantilla == "clave_cambiada" for m in correo_enviado)
    assert pyotp  # (módulo usado indirectamente por el fixture de TOTP)


def test_cambiar_clave_exige_la_clave_actual_correcta(crear_usuario, crear_request, clave_buena, codigo_totp):
    usuario = crear_usuario()
    request, _ = _login(crear_request, usuario, clave_buena)
    conf = servicios.obtener_configuracion_mfa(request)
    servicios.confirmar_configuracion_mfa(request, codigo_totp(conf.secreto))
    with pytest.raises(servicios.CredencialesInvalidas):
        servicios.cambiar_clave(request, "otra-clave-equivocada", "Nueva-Clave-Larga-9182")


def test_dispositivo_nuevo_envia_aviso_neutral_sin_la_ip(crear_usuario, crear_request, clave_buena, correo_enviado):
    usuario = crear_usuario()
    _login(crear_request, usuario, clave_buena, _dispositivo(1))
    assert not [m for m in correo_enviado if m.plantilla == "dispositivo_nuevo"]  # el primer ingreso no avisa
    _login(crear_request, usuario, clave_buena, _dispositivo(2))
    avisos = [m for m in correo_enviado if m.plantilla == "dispositivo_nuevo"]
    assert len(avisos) == 1
    assert "203.0.113.7" not in avisos[0].cuerpo and "Si no fue usted" in avisos[0].cuerpo
    _login(crear_request, usuario, clave_buena, _dispositivo(2))  # dispositivo ya conocido: sin aviso nuevo
    assert len([m for m in correo_enviado if m.plantilla == "dispositivo_nuevo"]) == 1


def test_la_sesion_no_guarda_ni_muestra_la_ip_completa(crear_usuario, crear_request, clave_buena):
    usuario = crear_usuario()
    _login(crear_request, usuario, clave_buena)
    fila = SesionDispositivo.objects.get()
    assert fila.ip_aproximada == "203.0.x.x" and "203.0.113.7" not in str(fila.__dict__)


def test_cerrar_sesion_revoca_la_fila_y_la_sesion(crear_usuario, crear_request, clave_buena):
    usuario = crear_usuario()
    request, _ = _login(crear_request, usuario, clave_buena)
    clave_sesion = request.session.session_key
    servicios.cerrar_sesion(request)
    assert not Session.objects.filter(session_key=clave_sesion).exists()
    assert SesionDispositivo.objects.get(session_key=clave_sesion).motivo_revocacion == "CIERRE_VOLUNTARIO"


def test_iniciar_sesion_estando_ya_autenticado_en_el_mismo_navegador_no_falla_y_reemplaza_la_sesion(crear_usuario, clave_buena):
    """Regresión: el mismo dispositivo reemplaza su sesión; no debe intentar guardar una sesión ya revocada (500)."""
    usuario = crear_usuario()
    cliente = Client()
    for _ in range(2):
        r = cliente.post(reverse("cuentas:ingresar"), {"cedula": usuario.cedula, "clave": clave_buena}, secure=True)
        assert r.status_code == 302
    assert cliente.get(reverse("cuentas:sesiones"), secure=True).status_code == 200
    assert SesionDispositivo.objects.filter(usuario=usuario, revocada_en__isnull=True).count() == 1

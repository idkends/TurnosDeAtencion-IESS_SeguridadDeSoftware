"""Flujos completos a través de las vistas (como los recorrería una persona con el navegador)."""
import re
from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse

from apps.cuentas.modelos import Usuario
from config import reloj

pytestmark = pytest.mark.django_db
HTTPS = {"secure": True}


def _ingresar(cliente, usuario, clave, **extra):
    return cliente.post(reverse("cuentas:ingresar"), {"cedula": usuario.cedula, "clave": clave, **extra}, **HTTPS)


def _codigo(texto_correo):
    return re.search(r"es (\d{8})\.", texto_correo).group(1)


def test_flujo_de_activacion_por_las_vistas_y_primer_ingreso(afiliar, correo_enviado, clave_buena):
    datos = afiliar()
    cliente = Client()
    pagina = cliente.post(reverse("cuentas:activar"), {"cedula": datos.cedula}, **HTTPS)
    assert pagina.status_code == 200 and "Revise su contacto registrado" in pagina.content.decode()
    enviado = correo_enviado[-1]
    assert enviado.destino == datos.correo
    r = cliente.post(reverse("cuentas:activar_confirmar"), {"cedula": datos.cedula, "codigo": _codigo(enviado.cuerpo), "clave_nueva": clave_buena, "clave_nueva2": clave_buena}, **HTTPS)
    assert r.status_code == 302 and r["Location"] == reverse("cuentas:ingresar")
    r = _ingresar(cliente, Usuario.objects.get(cedula=datos.cedula), clave_buena)
    assert r.status_code == 302 and r["Location"] == reverse("inicio")
    assert "Bienvenido" in cliente.get(reverse("inicio"), **HTTPS).content.decode()


def test_la_activacion_por_vistas_rechaza_claves_distintas_o_debiles(afiliar, correo_enviado):
    datos = afiliar()
    cliente = Client()
    cliente.post(reverse("cuentas:activar"), {"cedula": datos.cedula}, **HTTPS)
    codigo = _codigo(correo_enviado[-1].cuerpo)
    r = cliente.post(reverse("cuentas:activar_confirmar"), {"cedula": datos.cedula, "codigo": codigo, "clave_nueva": "Clave-Larga-Uno-123", "clave_nueva2": "otra-distinta"}, **HTTPS)
    assert "no coinciden" in r.content.decode()
    r = cliente.post(reverse("cuentas:activar_confirmar"), {"cedula": datos.cedula, "codigo": codigo, "clave_nueva": "password1234", "clave_nueva2": "password1234"}, **HTTPS)
    assert r.status_code == 200 and "común" in r.content.decode()
    assert Usuario.objects.get(cedula=datos.cedula).estado == "PENDIENTE_ACTIVACION"


def test_funcionario_primer_ingreso_configura_totp_recibe_codigos_y_entra(crear_usuario, clave_buena, codigo_totp):
    usuario = crear_usuario("RECEPCION")
    cliente = Client()
    r = _ingresar(cliente, usuario, clave_buena)
    assert r.status_code == 302 and r["Location"].startswith(reverse("cuentas:mfa_configurar"))
    assert cliente.get(reverse("inicio"), **HTTPS).status_code == 200  # (la página pública) pero sin sesión:
    assert "Bienvenido" not in cliente.get(reverse("inicio"), **HTTPS).content.decode()
    pagina = cliente.get(r["Location"], **HTTPS).content.decode()
    secreto = re.search(r"<strong>([A-Z2-7]{32})</strong>", pagina).group(1)
    assert "<svg" in pagina and "otpauth" not in pagina  # el QR se muestra; el URI no queda en el HTML como texto
    r = cliente.post(reverse("cuentas:mfa_configurar"), {"codigo": codigo_totp(secreto)}, **HTTPS)
    cuerpo = r.content.decode()
    assert r.status_code == 200 and "Guarde sus códigos de recuperación" in cuerpo
    assert len(re.findall(r"<li>[0-9a-f]{5}(?:-[0-9a-f]{5}){3}</li>", cuerpo)) == 10
    assert "Bienvenido" in cliente.get(reverse("inicio"), **HTTPS).content.decode()  # ya tiene sesión


def test_configurar_mfa_con_codigo_malo_reutiliza_el_mismo_secreto(crear_usuario, clave_buena):
    usuario = crear_usuario("SOPORTE")
    cliente = Client()
    r = _ingresar(cliente, usuario, clave_buena)
    antes = re.search(r"<strong>([A-Z2-7]{32})</strong>", cliente.get(r["Location"], **HTTPS).content.decode()).group(1)
    r2 = cliente.post(reverse("cuentas:mfa_configurar"), {"codigo": "000000"}, **HTTPS)
    assert r2.status_code == 200 and "no es válido" in r2.content.decode()
    despues = re.search(r"<strong>([A-Z2-7]{32})</strong>", r2.content.decode()).group(1)
    assert antes == despues


def test_login_con_totp_por_las_vistas(crear_usuario, clave_buena, codigo_totp):
    usuario = crear_usuario("AUDITOR_DPD")
    cliente = Client()
    r = _ingresar(cliente, usuario, clave_buena)
    secreto = re.search(r"<strong>([A-Z2-7]{32})</strong>", cliente.get(r["Location"], **HTTPS).content.decode()).group(1)
    cliente.post(reverse("cuentas:mfa_configurar"), {"codigo": codigo_totp(secreto)}, **HTTPS)
    cliente.post(reverse("cuentas:salir"), **HTTPS)
    reloj.avanzar(timedelta(seconds=120))
    r = _ingresar(cliente, usuario, clave_buena)
    assert r.status_code == 302 and r["Location"].startswith(reverse("cuentas:mfa"))
    mal = cliente.post(reverse("cuentas:mfa"), {"codigo": "123456"}, **HTTPS)
    assert mal.status_code == 200 and "no es válido" in mal.content.decode()
    ok = cliente.post(reverse("cuentas:mfa"), {"codigo": codigo_totp(secreto)}, **HTTPS)
    assert ok.status_code == 302 and "Bienvenido" in cliente.get(reverse("inicio"), **HTTPS).content.decode()


def test_la_pagina_de_mfa_sin_preautenticacion_redirige_al_ingreso():
    assert Client().get(reverse("cuentas:mfa"), **HTTPS)["Location"] == reverse("cuentas:ingresar")
    assert Client().get(reverse("cuentas:mfa_configurar"), **HTTPS)["Location"] == reverse("cuentas:ingresar")


def test_listar_y_revocar_sesiones_por_las_vistas(crear_usuario, clave_buena):
    usuario = crear_usuario()
    a, b = Client(), Client()
    a.cookies["__Host-dispositivo"], b.cookies["__Host-dispositivo"] = "dispositivo-prueba-aaaa-0001", "dispositivo-prueba-bbbb-0002"
    _ingresar(a, usuario, clave_buena)
    _ingresar(b, usuario, clave_buena)
    pagina = a.get(reverse("cuentas:sesiones"), **HTTPS).content.decode()
    assert "(esta sesión)" in pagina and pagina.count("Cerrar</button>") == 2
    ids = re.findall(r"/cuenta/sesiones/([0-9a-f-]{36})/revocar/", pagina)
    for i in ids:  # revoca todas; una de ellas es la de B y otra la propia
        a.post(reverse("cuentas:revocar_sesion", args=[i]), **HTTPS)
    assert b.get(reverse("cuentas:sesiones"), **HTTPS).status_code == 302  # la sesión de B ya no existe


def test_revocar_una_sesion_inexistente_o_ajena_da_404(crear_usuario, clave_buena):
    usuario, otro = crear_usuario(), crear_usuario()
    a, b = Client(), Client()
    _ingresar(a, usuario, clave_buena)
    _ingresar(b, otro, clave_buena)
    id_de_b = re.search(r"/cuenta/sesiones/([0-9a-f-]{36})/revocar/", b.get(reverse("cuentas:sesiones"), **HTTPS).content.decode()).group(1)
    assert a.post(reverse("cuentas:revocar_sesion", args=[id_de_b]), **HTTPS).status_code == 404
    assert a.post(reverse("cuentas:revocar_sesion", args=["00000000-0000-0000-0000-000000000000"]), **HTTPS).status_code == 404


def test_cambiar_clave_por_las_vistas_pide_configurar_segundo_factor_y_luego_cierra_sesiones(crear_usuario, clave_buena, codigo_totp):
    usuario = crear_usuario()
    cliente = Client()
    _ingresar(cliente, usuario, clave_buena)
    r = cliente.post(reverse("cuentas:cambiar_clave"), {"clave_actual": clave_buena, "clave_nueva": "Nueva-Clave-Larga-9182", "clave_nueva2": "Nueva-Clave-Larga-9182"}, **HTTPS)
    assert r.status_code == 302 and r["Location"].startswith(reverse("cuentas:mfa_configurar"))  # sin 2.º factor, primero lo activa
    secreto = re.search(r"<strong>([A-Z2-7]{32})</strong>", cliente.get(reverse("cuentas:mfa_configurar"), **HTTPS).content.decode()).group(1)
    cliente.post(reverse("cuentas:mfa_configurar"), {"codigo": codigo_totp(secreto)}, **HTTPS)
    mal = cliente.post(reverse("cuentas:cambiar_clave"), {"clave_actual": "equivocada-123456", "clave_nueva": "Nueva-Clave-Larga-9182", "clave_nueva2": "Nueva-Clave-Larga-9182"}, **HTTPS)
    assert "no es correcta" in mal.content.decode()
    debil = cliente.post(reverse("cuentas:cambiar_clave"), {"clave_actual": clave_buena, "clave_nueva": "password1234", "clave_nueva2": "password1234"}, **HTTPS)
    assert "común" in debil.content.decode()
    ok = cliente.post(reverse("cuentas:cambiar_clave"), {"clave_actual": clave_buena, "clave_nueva": "Nueva-Clave-Larga-9182", "clave_nueva2": "Nueva-Clave-Larga-9182"}, **HTTPS)
    assert ok.status_code == 302 and ok["Location"] == reverse("cuentas:ingresar")
    assert cliente.get(reverse("cuentas:sesiones"), **HTTPS).status_code == 302  # todas las sesiones, incluida esta, cerradas


def test_reautenticar_por_las_vistas(crear_usuario, clave_buena, codigo_totp):
    usuario = crear_usuario()
    cliente = Client()
    _ingresar(cliente, usuario, clave_buena)
    secreto = re.search(r"<strong>([A-Z2-7]{32})</strong>", cliente.get(reverse("cuentas:mfa_configurar"), **HTTPS).content.decode()).group(1)
    cliente.post(reverse("cuentas:mfa_configurar"), {"codigo": codigo_totp(secreto)}, **HTTPS)
    reloj.avanzar(timedelta(minutes=12))  # el segundo factor deja de ser reciente
    r = cliente.post(reverse("cuentas:cambiar_clave"), {"clave_actual": clave_buena, "clave_nueva": "Nueva-Clave-Larga-9182", "clave_nueva2": "Nueva-Clave-Larga-9182"}, **HTTPS)
    assert r["Location"].startswith(reverse("cuentas:reautenticar"))
    mal = cliente.post(reverse("cuentas:reautenticar"), {"clave": clave_buena, "codigo": "000000"}, **HTTPS)
    assert "No pudimos verificar" in mal.content.decode()
    ok = cliente.post(reverse("cuentas:reautenticar"), {"clave": clave_buena, "codigo": codigo_totp(secreto)}, **HTTPS)
    assert ok.status_code == 302


def test_flujo_de_recuperacion_por_las_vistas(crear_usuario, correo_enviado):
    usuario = crear_usuario()
    cliente = Client()
    cliente.post(reverse("cuentas:recuperar"), {"cedula": usuario.cedula}, **HTTPS)
    enlace = re.search(r"/cuenta/restablecer/[^/\s]+/", correo_enviado[-1].cuerpo).group(0)
    nueva = "Cielo-Nublado-Rio-4417"
    r = cliente.post(enlace, {"clave_nueva": nueva, "clave_nueva2": nueva, "codigo_mfa": ""}, **HTTPS)
    assert r.status_code == 302 and r["Location"] == reverse("cuentas:ingresar")
    assert cliente.get(enlace, **HTTPS).status_code == 410  # el enlace ya no sirve
    assert _ingresar(cliente, usuario, nueva).status_code == 302


def test_el_cierre_de_sesion_por_post_limpia_la_sesion(crear_usuario, clave_buena):
    usuario = crear_usuario()
    cliente = Client()
    _ingresar(cliente, usuario, clave_buena)
    assert cliente.post(reverse("cuentas:salir"), **HTTPS).status_code == 302
    assert cliente.get(reverse("cuentas:sesiones"), **HTTPS).status_code == 302


def test_el_limite_de_solicitudes_de_activacion_responde_429(afiliar, parametro):
    datos = afiliar()
    parametro("activacion_solicitudes_por_hora_cedula", 1)
    cliente = Client()
    assert cliente.post(reverse("cuentas:activar"), {"cedula": datos.cedula}, **HTTPS).status_code == 200
    r = cliente.post(reverse("cuentas:activar"), {"cedula": datos.cedula}, **HTTPS)
    assert r.status_code == 429 and "Retry-After" in r

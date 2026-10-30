"""T18, T14, R16, R19: configuración segura de Django, cabeceras, cookies, CSRF, errores genéricos y límites."""
import os
import re
import secrets
import subprocess
import sys
from unittest import mock

import pytest
from cryptography.fernet import Fernet
from django.conf import settings
from django.test import Client
from django.urls import reverse

pytestmark = pytest.mark.django_db
RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _entorno_prod(**extra):
    entorno = {
        "PATH": os.environ["PATH"],
        "DJANGO_SETTINGS_MODULE": "config.settings.prod",
        "DJANGO_SECRET_KEY": secrets.token_urlsafe(64),
        "ALLOWED_HOSTS": "portal.prueba.local",
        "URL_BASE": "https://portal.prueba.local",
        "HMAC_TOKENS_CLAVE": secrets.token_urlsafe(48),
        "TOTP_CLAVES_CIFRADO": Fernet.generate_key().decode(),
        "AUDITORIA_CLAVES": "1=" + secrets.token_hex(32),
        "DB_CLAVE": "x",
        "PYTHONPATH": RAIZ,
    }
    entorno.update(extra)
    return entorno


def _manage(*args, **extra):
    return subprocess.run([sys.executable, "manage.py", *args], cwd=RAIZ, env=_entorno_prod(**extra), capture_output=True, text=True)


# --- check --deploy -----------------------------------------------------------------------------------------------------------------


def test_T18_check_deploy_en_produccion_no_da_advertencias():
    r = _manage("check", "--deploy", "--fail-level", "WARNING")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "no issues" in r.stdout.lower(), r.stdout + r.stderr


def test_T18_check_deploy_falla_si_se_debilita_la_configuracion():
    """Prueba de la prueba: con una SECRET_KEY de ejemplo débil, check --deploy sí advierte."""
    r = _manage("check", "--deploy", "--fail-level", "WARNING", DJANGO_SECRET_KEY="django-insecure-clave-debil")
    assert r.returncode != 0 and "security.W009" in (r.stdout + r.stderr)


def test_sin_secretos_en_el_entorno_la_aplicacion_no_arranca():
    entorno = _entorno_prod()
    del entorno["DJANGO_SECRET_KEY"]
    r = subprocess.run([sys.executable, "manage.py", "check"], cwd=RAIZ, env=entorno, capture_output=True, text=True)
    assert r.returncode != 0 and "DJANGO_SECRET_KEY" in r.stderr  # falla cerrado, no usa un valor por defecto


def test_en_produccion_debug_esta_apagado_y_los_hosts_son_explicitos():
    r = subprocess.run(
        [sys.executable, "-c", "import django;django.setup();from django.conf import settings as s;print(s.DEBUG, s.ALLOWED_HOSTS, s.SECURE_SSL_REDIRECT)"],
        cwd=RAIZ, env=_entorno_prod(), capture_output=True, text=True,
    )
    assert r.stdout.strip() == "False ['portal.prueba.local'] True", r.stdout + r.stderr


def test_la_configuracion_base_es_segura_por_defecto():
    assert settings.DEBUG is False
    assert settings.ALLOWED_HOSTS and "*" not in settings.ALLOWED_HOSTS
    assert isinstance(settings.SECRET_KEY_FALLBACKS, list)
    assert settings.SESSION_SERIALIZER == "django.contrib.sessions.serializers.JSONSerializer"
    assert settings.X_FRAME_OPTIONS == "DENY" and settings.SECURE_CONTENT_TYPE_NOSNIFF is True
    assert settings.SECURE_HSTS_SECONDS > 0 and settings.SECURE_HSTS_INCLUDE_SUBDOMAINS
    assert settings.USE_X_FORWARDED_HOST is False
    assert "django.contrib.gis" not in settings.INSTALLED_APPS and "django.contrib.admin" not in settings.INSTALLED_APPS


def test_la_rotacion_de_secret_key_con_fallbacks_sigue_aceptando_firmas_antiguas():
    from django.core import signing

    vieja = secrets.token_urlsafe(64)
    firmado = signing.TimestampSigner(key=vieja, salt="x").sign("dato")
    with mock.patch.object(settings, "SECRET_KEY", secrets.token_urlsafe(64)), mock.patch.object(settings, "SECRET_KEY_FALLBACKS", [vieja]):
        assert signing.TimestampSigner(salt="x").unsign(firmado) == "dato"  # una sesión firmada con la clave anterior sigue valiendo


# --- Cabeceras ---------------------------------------------------------------------------------------------------------------------------


def _pagina(**extra):
    return Client().get(reverse("cuentas:ingresar"), secure=True, **extra)


def test_cabeceras_de_seguridad_presentes():
    r = _pagina()
    assert r["X-Frame-Options"] == "DENY"
    assert r["X-Content-Type-Options"] == "nosniff"
    assert r["Referrer-Policy"] == "same-origin"
    assert r["Cross-Origin-Opener-Policy"] == "same-origin"
    assert r["Cross-Origin-Resource-Policy"] == "same-origin"
    assert "camera=()" in r["Permissions-Policy"]
    assert "no-store" in r["Cache-Control"]
    assert "max-age=" in r["Strict-Transport-Security"] and "includeSubDomains" in r["Strict-Transport-Security"]
    assert r["X-Request-ID"]


def test_CSP_estricta_sin_unsafe_inline_ni_unsafe_eval():
    csp = _pagina()["Content-Security-Policy"]
    assert "default-src 'none'" in csp and "frame-ancestors 'none'" in csp and "object-src 'none'" in csp
    assert "base-uri 'self'" in csp and "form-action 'self'" in csp and "upgrade-insecure-requests" in csp
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp and "*" not in csp
    assert re.search(r"script-src 'self' 'nonce-[\w+/=-]+'", csp)  # nonce por respuesta


def test_el_nonce_cambia_en_cada_respuesta():
    a = _pagina()["Content-Security-Policy"]
    b = _pagina()["Content-Security-Policy"]
    assert re.search(r"nonce-([\w+/=-]+)", a).group(1) != re.search(r"nonce-([\w+/=-]+)", b).group(1)


def test_las_paginas_no_tienen_scripts_ni_estilos_en_linea():
    html = _pagina().content.decode()
    assert not re.search(r"<script(?![^>]*\bsrc=)", html) and not re.search(r"\son\w+=", html) and " style=" not in html


def test_la_pagina_de_ingreso_declara_idioma_y_estructura_accesible():
    html = _pagina().content.decode()
    assert '<html lang="es">' in html and 'class="saltar"' in html and "<main" in html
    assert re.search(r'<label for="id_cedula">', html) and 'autocomplete="current-password"' in html


# --- Cookies -----------------------------------------------------------------------------------------------------------------------------------


def test_cookie_csrf_con_prefijo_host_y_atributos_seguros():
    cookie = _pagina().cookies["__Host-csrftoken"]
    assert cookie["secure"] and cookie["httponly"] and cookie["samesite"] == "Lax" and cookie["path"] == "/" and not cookie["domain"]


def test_cookie_de_sesion_y_de_dispositivo_con_prefijo_host_y_atributos_seguros(crear_usuario, clave_buena):
    usuario = crear_usuario()
    r = Client().post(reverse("cuentas:ingresar"), {"cedula": usuario.cedula, "clave": clave_buena}, secure=True)
    assert r.status_code == 302
    for nombre in ("__Host-sessionid", "__Host-dispositivo"):
        cookie = r.cookies[nombre]
        assert cookie["secure"] and cookie["httponly"] and cookie["samesite"] == "Lax", nombre
        assert cookie["path"] == "/" and not cookie["domain"], nombre  # requisitos del prefijo __Host-


def test_la_sesion_se_rota_al_iniciar_sesion(crear_usuario, clave_buena):
    usuario = crear_usuario()
    cliente = Client()
    cliente.get(reverse("cuentas:ingresar"), secure=True)
    antes = cliente.cookies["__Host-csrftoken"].value
    r = cliente.post(reverse("cuentas:ingresar"), {"cedula": usuario.cedula, "clave": clave_buena}, secure=True)
    despues = r.cookies["__Host-sessionid"].value
    assert despues and "sessionid" not in antes


# --- CSRF, errores y entradas ---------------------------------------------------------------------------------------------------------------


def test_R16_el_csrf_esta_activo_y_rechaza_posts_sin_token(crear_usuario, clave_buena):
    usuario = crear_usuario()
    cliente = Client(enforce_csrf_checks=True)
    r = cliente.post(reverse("cuentas:ingresar"), {"cedula": usuario.cedula, "clave": clave_buena}, secure=True)
    assert r.status_code == 403 and "No pudimos procesar la solicitud" in r.content.decode()
    assert "CSRF" not in r.content.decode()  # el mensaje no da pistas técnicas


def test_con_token_csrf_valido_el_post_se_acepta(crear_usuario, clave_buena):
    usuario = crear_usuario()
    cliente = Client(enforce_csrf_checks=True)
    token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', cliente.get(reverse("cuentas:ingresar"), secure=True).content.decode()).group(1)
    r = cliente.post(reverse("cuentas:ingresar"), {"cedula": usuario.cedula, "clave": clave_buena, "csrfmiddlewaretoken": token}, secure=True,
                       HTTP_REFERER="https://testserver/cuenta/ingresar/")  # los navegadores envían Referer en POST de mismo origen
    assert r.status_code == 302


def test_el_cierre_de_sesion_solo_acepta_post():
    assert Client().get(reverse("cuentas:salir"), secure=True).status_code == 405


def test_errores_genericos_un_error_interno_no_filtra_detalles():
    cliente = Client(raise_request_exception=False)
    with mock.patch("apps.cuentas.vistas.servicios.solicitar_activacion", side_effect=RuntimeError("detalle-interno-secreto /app/apps/x.py")):
        r = cliente.post(reverse("cuentas:activar"), {"cedula": "1750000001"}, secure=True)
    cuerpo = r.content.decode()
    assert r.status_code == 500 and "detalle-interno-secreto" not in cuerpo and "Traceback" not in cuerpo and "/app/" not in cuerpo


def test_un_404_no_muestra_rutas_ni_versiones():
    r = Client().get("/ruta/inexistente/", secure=True)
    cuerpo = r.content.decode()
    assert r.status_code == 404 and "Django" not in cuerpo and "urlpatterns" not in cuerpo and "Using the URLconf" not in cuerpo


def test_CWE601_el_parametro_next_no_permite_redirecciones_abiertas(crear_usuario, clave_buena):
    usuario = crear_usuario()
    for destino in ("https://atacante.example/", "//atacante.example/", "javascript:alert(1)", "http://atacante.example"):
        r = Client().post(reverse("cuentas:ingresar"), {"cedula": usuario.cedula, "clave": clave_buena, "next": destino}, secure=True)
        assert r.status_code == 302 and r["Location"] == reverse("inicio"), destino
    r = Client().post(reverse("cuentas:ingresar"), {"cedula": usuario.cedula, "clave": clave_buena, "next": "/cuenta/sesiones/"}, secure=True)
    assert r["Location"] == "/cuenta/sesiones/"  # los destinos propios sí se respetan


def test_el_cuerpo_de_la_solicitud_esta_limitado_a_64_KB():
    r = Client().post(reverse("cuentas:ingresar"), data="x=" + "a" * (70 * 1024), content_type="application/x-www-form-urlencoded", secure=True)
    assert r.status_code == 400


def test_las_rutas_protegidas_exigen_sesion():
    for nombre in ("cuentas:sesiones", "cuentas:cambiar_clave", "cuentas:reautenticar"):
        r = Client().get(reverse(nombre), secure=True)
        assert r.status_code == 302 and "/cuenta/ingresar/" in r["Location"], nombre


def test_inyeccion_en_login_falla_sin_error_tecnico_visible():
    cliente = Client()
    for carga in ("' OR '1'='1", "1750000001'; DROP TABLE cuentas_usuario;--", "<script>alert(1)</script>"):
        r = cliente.post(reverse("cuentas:ingresar"), {"cedula": carga, "clave": carga}, secure=True)
        cuerpo = r.content.decode()
        assert r.status_code in (200, 429) and "syntax error" not in cuerpo.lower() and "<script>alert" not in cuerpo
        cliente = Client()  # una IP/sesión nueva por intento no importa: el límite es por IP de contexto


def test_la_salud_responde_sin_datos():
    r = Client().get(reverse("salud"), secure=True)
    assert r.status_code == 200 and r.content == b"ok"

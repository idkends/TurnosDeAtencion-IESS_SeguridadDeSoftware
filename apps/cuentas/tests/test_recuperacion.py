"""Recuperación de clave (Anexo F, CA-06, CA-16): token de 128 bits como hash, 15 min, un solo uso, dominio fijo."""
import re
import threading
import time
from datetime import timedelta

import pytest
from django.db import connection
from django.test import Client
from django.urls import reverse

from apps.cuentas import servicios
from apps.cuentas.modelos import SesionDispositivo, TokenRecuperacion
from config import reloj
from config.cedula import sintetica

pytestmark = pytest.mark.django_db
NUEVA = "Cielo-Nublado-Rio-4417"


def _token(correo_enviado):
    return re.search(r"/cuenta/restablecer/([^/\s]+)/", correo_enviado[-1].cuerpo).group(1)


def test_el_enlace_llega_al_contacto_registrado_con_dominio_fijo(crear_usuario, correo_enviado, settings):
    usuario = crear_usuario(correo="titular@correo.iess-prueba.example")
    servicios.solicitar_recuperacion(usuario.cedula)
    assert [m.destino for m in correo_enviado] == ["titular@correo.iess-prueba.example"]
    assert settings.URL_BASE + "/cuenta/restablecer/" in correo_enviado[0].cuerpo


def test_el_token_tiene_128_bits_y_se_guarda_solo_como_hash(crear_usuario, correo_enviado):
    usuario = crear_usuario()
    servicios.solicitar_recuperacion(usuario.cedula)
    token = _token(correo_enviado)
    assert len(token) >= 22  # 16 bytes en base64 url-safe = 22 caracteres (128 bits)
    fila = TokenRecuperacion.objects.get()
    assert token not in fila.token_hash and len(fila.token_hash) == 64


def test_CA06_el_token_sirve_una_sola_vez(crear_usuario, correo_enviado):
    usuario = crear_usuario()
    servicios.solicitar_recuperacion(usuario.cedula)
    token = _token(correo_enviado)
    servicios.restablecer_clave(token, NUEVA)
    with pytest.raises(servicios.TokenInvalido):
        servicios.restablecer_clave(token, "Otra-Clave-Distinta-5521")
    usuario.refresh_from_db()
    assert usuario.check_password(NUEVA)  # la segunda solicitud no cambió nada


def test_CA06_el_token_vence_a_los_15_minutos(crear_usuario, correo_enviado):
    usuario = crear_usuario()
    servicios.solicitar_recuperacion(usuario.cedula)
    token = _token(correo_enviado)
    reloj.avanzar(timedelta(minutes=15, seconds=1))
    assert servicios.token_recuperacion_vigente(token) is False
    with pytest.raises(servicios.TokenInvalido):
        servicios.restablecer_clave(token, NUEVA)


def test_un_token_nuevo_invalida_el_anterior(crear_usuario, correo_enviado):
    usuario = crear_usuario()
    servicios.solicitar_recuperacion(usuario.cedula)
    primero = _token(correo_enviado)
    servicios.solicitar_recuperacion(usuario.cedula)
    with pytest.raises(servicios.TokenInvalido):
        servicios.restablecer_clave(primero, NUEVA)


def test_token_inventado_o_vacio_es_invalido():
    for basura in ("", "x", "A" * 22, "../../etc/passwd"):
        with pytest.raises(servicios.TokenInvalido):
            servicios.restablecer_clave(basura, NUEVA)


def test_CA05_mensaje_y_respuesta_iguales_exista_o_no_la_cedula(crear_usuario, correo_enviado):
    usuario = crear_usuario()
    cliente = Client()

    def pedir(cedula):
        r = cliente.post(reverse("cuentas:recuperar"), {"cedula": cedula})
        cuerpo = re.sub(r'name="csrfmiddlewaretoken" value="[^"]+"', "", r.content.decode())
        return r.status_code, cuerpo, [t.name for t in r.templates]

    assert pedir(usuario.cedula) == pedir(sintetica(888891))
    assert len(correo_enviado) == 1  # solo la cuenta real recibió el enlace


def test_CA05_el_tiempo_de_respuesta_no_delata_si_la_cedula_existe(crear_usuario):
    usuario = crear_usuario()

    def medir(cedula):
        inicio = time.monotonic()
        servicios.solicitar_recuperacion(cedula)
        return time.monotonic() - inicio

    con = sorted(medir(usuario.cedula) for _ in range(3))[1]
    sin = sorted(medir(sintetica(888892)) for _ in range(3))[1]
    assert abs(con - sin) < 0.08, (con, sin)


def test_CA16_un_Host_falso_se_rechaza_y_no_genera_enlaces(crear_usuario, correo_enviado):
    usuario = crear_usuario()
    r = Client().post(reverse("cuentas:recuperar"), {"cedula": usuario.cedula}, HTTP_HOST="atacante.example")
    assert r.status_code == 400  # ALLOWED_HOSTS lo rechaza antes de llegar a la vista
    assert correo_enviado == [] and not TokenRecuperacion.objects.exists()


def test_CA16_aun_con_un_host_permitido_el_enlace_usa_el_dominio_de_la_configuracion(crear_usuario, correo_enviado, settings):
    usuario = crear_usuario()
    Client().post(reverse("cuentas:recuperar"), {"cedula": usuario.cedula}, HTTP_HOST="localhost", HTTP_X_FORWARDED_HOST="atacante.example")
    enlace = re.search(r"https?://\S+", correo_enviado[0].cuerpo).group(0)
    assert enlace.startswith(settings.URL_BASE + "/") and "atacante" not in enlace and "localhost" not in enlace


def test_restablecer_cierra_todas_las_sesiones_y_avisa(crear_usuario, crear_request, clave_buena, correo_enviado):
    usuario = crear_usuario()
    for n in (1, 2):
        servicios.autenticar(crear_request(cookies={"__Host-dispositivo": f"dispositivo-prueba-numero-{n:04d}-xxxx"}), usuario.cedula, clave_buena)
    assert SesionDispositivo.objects.filter(usuario=usuario, revocada_en__isnull=True).count() == 2
    servicios.solicitar_recuperacion(usuario.cedula)
    servicios.restablecer_clave(_token(correo_enviado), NUEVA)
    assert SesionDispositivo.objects.filter(usuario=usuario, revocada_en__isnull=True).count() == 0
    assert any(m.plantilla == "clave_cambiada" for m in correo_enviado)


def test_una_clave_rechazada_no_consume_el_token(crear_usuario, correo_enviado):
    usuario = crear_usuario()
    servicios.solicitar_recuperacion(usuario.cedula)
    token = _token(correo_enviado)
    with pytest.raises(servicios.ClaveRechazada):
        servicios.restablecer_clave(token, "corta")
    assert servicios.token_recuperacion_vigente(token)
    servicios.restablecer_clave(token, NUEVA)  # con una clave buena sí funciona


def test_quien_tiene_segundo_factor_debe_presentarlo_para_restablecer(crear_usuario, crear_request, clave_buena, codigo_totp, correo_enviado):
    usuario = crear_usuario()
    request = crear_request()
    servicios.autenticar(request, usuario.cedula, clave_buena)
    conf = servicios.obtener_configuracion_mfa(request)
    servicios.confirmar_configuracion_mfa(request, codigo_totp(conf.secreto))
    servicios.solicitar_recuperacion(usuario.cedula)
    token = _token(correo_enviado)
    with pytest.raises(servicios.CodigoInvalido):  # leer el correo no basta
        servicios.restablecer_clave(token, NUEVA, "")
    reloj.avanzar(timedelta(seconds=60))
    servicios.restablecer_clave(token, NUEVA, codigo_totp(conf.secreto))


def test_la_pagina_del_token_no_filtra_el_token_por_referer(crear_usuario, correo_enviado):
    usuario = crear_usuario()
    servicios.solicitar_recuperacion(usuario.cedula)
    r = Client().get(reverse("cuentas:restablecer", args=[_token(correo_enviado)]))
    assert r.status_code == 200 and r["Referrer-Policy"] == "no-referrer"
    assert "no-store" in r["Cache-Control"]


def test_token_invalido_devuelve_410_sin_detalles():
    r = Client().get(reverse("cuentas:restablecer", args=["A" * 22]))
    assert r.status_code == 410


@pytest.mark.django_db(transaction=True)
def test_CA06_dos_solicitudes_simultaneas_con_el_mismo_token_solo_una_gana(crear_usuario, correo_enviado):
    usuario = crear_usuario()
    servicios.solicitar_recuperacion(usuario.cedula)
    token = _token(correo_enviado)
    resultados, barrera = [], threading.Barrier(2)

    def intentar(clave):
        try:
            barrera.wait()
            servicios.restablecer_clave(token, clave)
            resultados.append(("ok", clave))
        except servicios.TokenInvalido:
            resultados.append(("rechazado", clave))
        finally:
            connection.close()

    hilos = [threading.Thread(target=intentar, args=(c,)) for c in ("Primera-Clave-Larga-7731", "Segunda-Clave-Larga-8842")]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    assert sorted(r[0] for r in resultados) == ["ok", "rechazado"], resultados
    ganadora = next(c for estado, c in resultados if estado == "ok")
    usuario.refresh_from_db()
    assert usuario.check_password(ganadora)

"""Inicio de sesión: respuesta indistinguible (CA-05), bloqueo progresivo y límite por IP (R05, R18, T03)."""
import re
import time
from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse

from apps.cuentas import servicios
from config import contexto, reloj
from config.cedula import sintetica

pytestmark = pytest.mark.django_db
MALA = "Clave-Incorrecta-000"


def _fallar(crear_request, cedula, veces, clave=MALA):
    for _ in range(veces):
        with pytest.raises(servicios.CredencialesInvalidas):
            servicios.autenticar(crear_request(), cedula, clave)


def test_login_correcto_crea_sesion(crear_usuario, crear_request, clave_buena):
    usuario = crear_usuario()
    request = crear_request()
    resultado = servicios.autenticar(request, usuario.cedula, clave_buena)
    assert resultado.estado == "AUTENTICADO"
    assert request.user.is_authenticated and request.user.id == usuario.id
    assert resultado.cookie_dispositivo  # se pide guardar la cookie de dispositivo


def test_CA05_cedula_inexistente_y_clave_incorrecta_dan_el_mismo_mensaje_y_estado(crear_usuario, clave_buena):
    existente = crear_usuario()
    cliente = Client()

    def intentar(cedula, clave):
        r = cliente.post(reverse("cuentas:ingresar"), {"cedula": cedula, "clave": clave})
        cuerpo = r.content.decode()
        cuerpo = re.sub(r'name="csrfmiddlewaretoken" value="[^"]+"', "", cuerpo)
        cuerpo = re.sub(r'nonce="[^"]+"', 'nonce=""', cuerpo)  # el nonce de la CSP cambia en cada respuesta
        cuerpo = re.sub(r'value="\d{10}"', 'value=""', cuerpo)  # el formulario devuelve lo que la persona escribió
        return r.status_code, cuerpo, [t.name for t in r.templates]

    clave_mala_existente = intentar(existente.cedula, MALA)
    cedula_inexistente = intentar(sintetica(888888), MALA)
    assert clave_mala_existente == cedula_inexistente  # mismo código HTTP, mismo HTML, misma plantilla
    assert "No fue posible iniciar sesión" in clave_mala_existente[1]
    assert "Clave-Incorrecta" not in clave_mala_existente[1]  # nunca se devuelve la clave


def test_CA05_el_tiempo_de_respuesta_no_delata_la_cuenta(crear_usuario, crear_request):
    existente = crear_usuario()

    def medir(cedula, i):
        r = crear_request()
        contexto.fijar(ip=f"198.51.100.{i}")  # IPs distintas: no importa el límite por IP aquí
        inicio = time.monotonic()
        with pytest.raises(servicios.CredencialesInvalidas):
            servicios.autenticar(r, cedula, MALA)
        return time.monotonic() - inicio

    con = sorted(medir(existente.cedula, i) for i in range(1, 4))[1]
    sin = sorted(medir(sintetica(888889), i) for i in range(10, 13))[1]
    assert abs(con - sin) < 0.08, (con, sin)


def test_cuenta_pendiente_o_suspendida_no_entra_y_se_ve_igual(crear_usuario, crear_request, clave_buena):
    pendiente = crear_usuario(estado="PENDIENTE_ACTIVACION")
    suspendida = crear_usuario(estado="SUSPENDIDA")
    for u in (pendiente, suspendida):
        with pytest.raises(servicios.CredencialesInvalidas):
            servicios.autenticar(crear_request(), u.cedula, clave_buena)


def test_R05_bloqueo_progresivo_1_5_15_y_60_minutos(crear_usuario, crear_request, clave_buena, parametro):
    parametro("login_intentos_por_minuto_ip", 10_000)  # aquí se prueba el bloqueo por cuenta, no el límite por IP
    usuario = crear_usuario()
    for minutos in (1, 5, 15, 60, 60):  # el último escalón se mantiene en 60 min
        _fallar(crear_request, usuario.cedula, 5)  # cada 5 fallos seguidos suben el escalón
        # Durante la espera ni la clave CORRECTA entra, y se ve igual que un fallo normal.
        with pytest.raises(servicios.CredencialesInvalidas):
            servicios.autenticar(crear_request(), usuario.cedula, clave_buena)
        reloj.avanzar(timedelta(minutes=minutos) - timedelta(seconds=5))
        with pytest.raises(servicios.CredencialesInvalidas):
            servicios.autenticar(crear_request(), usuario.cedula, clave_buena)  # faltan 5 s: sigue bloqueada
        reloj.avanzar(timedelta(seconds=6))  # termina la espera
    assert servicios.autenticar(crear_request(), usuario.cedula, clave_buena).estado == "AUTENTICADO"


def test_login_exitoso_reinicia_el_contador_de_fallos(crear_usuario, crear_request, clave_buena):
    usuario = crear_usuario()
    _fallar(crear_request, usuario.cedula, 4)
    servicios.autenticar(crear_request(), usuario.cedula, clave_buena)  # entra: contador a cero
    _fallar(crear_request, usuario.cedula, 4)  # 4 más no bloquean
    assert servicios.autenticar(crear_request(), usuario.cedula, clave_buena).estado == "AUTENTICADO"


def test_el_bloqueo_tambien_aplica_a_cedulas_inexistentes_para_no_delatarlas(crear_request):
    cedula = sintetica(888890)
    _fallar(crear_request, cedula, 5)
    for _ in range(2):  # sigue fallando igual: no hay diferencia observable con una cuenta real bloqueada
        with pytest.raises(servicios.CredencialesInvalidas):
            servicios.autenticar(crear_request(), cedula, MALA)
    assert servicios._bloqueada(cedula)


def test_R05_diez_intentos_por_minuto_por_ip(crear_usuario, crear_request):
    usuario = crear_usuario()
    for _ in range(10):
        with pytest.raises(servicios.CredencialesInvalidas):
            servicios.autenticar(crear_request(), usuario.cedula, MALA)
        servicios._limpiar_fallos(usuario.cedula)  # aislar el límite por IP del bloqueo por cuenta
    with pytest.raises(servicios.LimiteExcedido) as error:
        servicios.autenticar(crear_request(), usuario.cedula, MALA)
    assert 1 <= error.value.reintentar_en <= 60


def test_el_limite_por_ip_responde_429_con_retry_after(crear_usuario):
    usuario = crear_usuario()
    cliente = Client()
    ultimo = None
    for _ in range(11):
        ultimo = cliente.post(reverse("cuentas:ingresar"), {"cedula": usuario.cedula, "clave": MALA})
        servicios._limpiar_fallos(usuario.cedula)
    assert ultimo.status_code == 429 and int(ultimo["Retry-After"]) >= 1


def test_el_limite_de_una_ip_no_bloquea_a_otra(crear_usuario, crear_request, clave_buena):
    usuario = crear_usuario()
    for _ in range(10):
        with pytest.raises(servicios.CredencialesInvalidas):
            servicios.autenticar(crear_request(), usuario.cedula, MALA)
        servicios._limpiar_fallos(usuario.cedula)
    contexto.fijar(ip="198.51.100.99")  # otra persona, otra IP
    assert servicios.autenticar(crear_request(), usuario.cedula, clave_buena).estado == "AUTENTICADO"


def test_clave_demasiado_larga_siempre_falla_aunque_su_inicio_coincida(crear_usuario, crear_request, clave_buena):
    usuario = crear_usuario()
    with pytest.raises(servicios.CredencialesInvalidas):
        servicios.autenticar(crear_request(), usuario.cedula, clave_buena + "x" * 200)


def test_los_fallos_y_bloqueos_quedan_en_la_auditoria_sin_la_cedula_ni_la_clave(crear_usuario, crear_request):
    from apps.auditoria.modelos import EventoAuditoria

    usuario = crear_usuario()
    _fallar(crear_request, usuario.cedula, 5, clave="Clave-Secreta-XYZ-123")
    eventos = list(EventoAuditoria.objects.filter(accion="LOGIN_FALLIDO"))
    assert len(eventos) == 5
    volcado = " ".join(str(e.detalle) for e in eventos)
    assert usuario.cedula not in volcado and "Clave-Secreta-XYZ-123" not in volcado
    assert any(e.detalle.get("bloqueo_nuevo") for e in eventos)

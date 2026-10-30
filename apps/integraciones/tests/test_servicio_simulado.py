"""El servicio de afiliación SIMULADO real (el del contenedor `afiliacion_sim`) frente al adaptador HTTP real."""
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from apps.integraciones import servicio_token
from apps.integraciones.adaptadores.http import AfiliacionHttp
from apps.integraciones.puertos import ServicioNoDisponible
from apps.integraciones.simulados import afiliacion_servidor as sim
from apps.integraciones.sinteticos import generar_afiliados
from config.cedula import sintetica

SECRETO = b"secreto-del-simulado-de-prueba"


@pytest.fixture
def servidor(monkeypatch):
    monkeypatch.setattr(sim, "SECRETO", SECRETO)
    monkeypatch.setitem(sim._estado, "modo", "normal")
    monkeypatch.setitem(sim._estado, "latencia_ms", 0)
    http = ThreadingHTTPServer(("127.0.0.1", 0), sim.Manejador)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{http.server_address[1]}"
    http.shutdown()


@pytest.fixture
def adaptador(servidor, parametro):
    parametro("integracion_reintentos", 0)
    parametro("integracion_timeout_segundos", 0.5)
    return AfiliacionHttp(servidor, SECRETO, dormir=lambda s: None)


def _control(url, **cuerpo):
    ruta = "/control"
    peticion = urllib.request.Request(url + ruta, json.dumps(cuerpo).encode(), method="POST", headers={"X-Servicio-Token": servicio_token.firmar(SECRETO, ruta)})
    return json.loads(urllib.request.urlopen(peticion, timeout=2).read())


def test_devuelve_datos_sinteticos_validos_de_un_afiliado(adaptador):
    esperado = generar_afiliados()[2]
    datos = adaptador.consultar(esperado["cedula"])
    assert (datos.nombres, datos.correo, datos.vigente) == (esperado["nombres"], esperado["correo"], True)


def test_los_afiliados_no_vigentes_se_reportan_como_tales(adaptador):
    no_vigente = next(a for a in generar_afiliados() if not a["vigente"])
    assert adaptador.consultar(no_vigente["cedula"]).vigente is False


def test_una_cedula_valida_que_no_consta_devuelve_none(adaptador):
    assert adaptador.consultar(sintetica(999)) is None  # cédula válida que no está en el registro: responde 404


def test_sin_token_o_con_token_ajeno_el_simulado_responde_401(servidor):
    for token in (None, "123.abc", servicio_token.firmar(b"otro-secreto", "/afiliados/1750000018")):
        cabeceras = {"X-Servicio-Token": token} if token else {}
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(urllib.request.Request(servidor + "/afiliados/1750000018", headers=cabeceras), timeout=2)
        assert error.value.code == 401


def test_el_simulado_puede_caerse_a_proposito_y_el_adaptador_lo_trata_como_no_disponible(servidor, adaptador):
    _control(servidor, modo="caido")
    with pytest.raises(ServicioNoDisponible):
        adaptador.consultar(generar_afiliados()[0]["cedula"])
    _control(servidor, modo="normal")
    assert adaptador.consultar(generar_afiliados()[0]["cedula"]) is not None


def test_el_simulado_puede_devolver_un_esquema_invalido_a_proposito(servidor, adaptador):
    _control(servidor, modo="esquema_invalido")
    with pytest.raises(ServicioNoDisponible):
        adaptador.consultar(generar_afiliados()[0]["cedula"])


def test_el_endpoint_de_control_tambien_exige_token(servidor):
    peticion = urllib.request.Request(servidor + "/control", b'{"modo": "caido"}', method="POST")
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(peticion, timeout=2)
    assert error.value.code == 401


@pytest.mark.django_db
def test_delegacion_vigente_se_evalua_en_cada_consulta(crear_usuario):
    from datetime import timedelta

    from apps.delegaciones import servicios
    from apps.delegaciones.modelos import Delegacion
    from config import reloj

    titular, rep = crear_usuario(), crear_usuario("REPRESENTANTE")
    assert servicios.delegacion_vigente(titular.id, rep.id) is False
    ahora = reloj.ahora()
    d = Delegacion.objects.create(titular=titular, representante=rep, inicio=ahora - timedelta(days=1), vence=ahora + timedelta(days=30))
    assert servicios.delegacion_vigente(titular.id, rep.id) is True
    assert servicios.delegacion_vigente(rep.id, titular.id) is False  # no es simétrica
    reloj.avanzar(timedelta(days=31))
    assert servicios.delegacion_vigente(titular.id, rep.id) is False  # venció
    reloj.restablecer()
    Delegacion.objects.filter(pk=d.pk).update(estado="REVOCADA")
    assert servicios.delegacion_vigente(titular.id, rep.id) is False  # revocada: surte efecto en la siguiente solicitud

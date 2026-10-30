"""Sistema de agenda (R07, RT-10, CA-15, T07): adaptador HTTP con tiempo de espera, reintentos, circuito, esquema e idempotencia."""
import json
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import ThreadingHTTPServer

import pytest

from apps.integraciones import servicio_token
from apps.integraciones.adaptadores.agenda import AgendaHttp, AgendaMemoria
from apps.integraciones.circuito import Circuito
from apps.integraciones.puertos import ServicioNoDisponible
from apps.integraciones.simulados import agenda_servidor as sim

SECRETO = b"secreto-de-la-agenda-de-prueba"


def _ref():
    return uuid.uuid4().hex


@pytest.fixture
def servidor(monkeypatch):
    monkeypatch.setattr(sim, "SECRETO", SECRETO)
    monkeypatch.setitem(sim._estado, "modo", "normal")
    monkeypatch.setitem(sim._estado, "latencia_ms", 0)
    monkeypatch.setattr(sim, "_confirmaciones", {})
    http = ThreadingHTTPServer(("127.0.0.1", 0), sim.Manejador)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{http.server_address[1]}"
    http.shutdown()


@pytest.fixture
def agenda(servidor, parametro):
    parametro("integracion_reintentos", 0)
    parametro("integracion_timeout_segundos", 0.4)
    parametro("integracion_espera_base_segundos", 0.0)
    return AgendaHttp(servidor, SECRETO, dormir=lambda s: None)


def _control(url, **cuerpo):
    peticion = urllib.request.Request(url + "/control", json.dumps(cuerpo).encode(), method="POST", headers={"X-Servicio-Token": servicio_token.firmar(SECRETO, "/control")})
    return json.loads(urllib.request.urlopen(peticion, timeout=2).read())


def _confirmar(agenda, clave=None, franja=None):
    return agenda.confirmar(turno_ref=_ref(), franja_ref=franja or _ref(), afiliado_ref=_ref(), clave=clave or _ref())


def test_la_agenda_confirma_y_devuelve_una_referencia(agenda):
    c = _confirmar(agenda)
    assert c.estado == "CONFIRMADA" and c.referencia.startswith("AG-")


def test_la_confirmacion_es_idempotente_por_clave(agenda):
    clave = _ref()
    assert _confirmar(agenda, clave) == _confirmar(agenda, clave)  # el reintento no crea otra reserva en la agenda


def test_la_agenda_puede_rechazar_a_proposito(agenda, servidor):
    _control(servidor, modo="rechaza")
    assert _confirmar(agenda).estado == "RECHAZADA"


def test_agenda_caida_es_servicio_no_disponible_y_se_recupera(agenda, servidor):
    _control(servidor, modo="caido")
    with pytest.raises(ServicioNoDisponible):
        _confirmar(agenda)
    _control(servidor, modo="normal")
    assert _confirmar(agenda).estado == "CONFIRMADA"


def test_CA15_tiempo_de_espera_agotado_es_servicio_no_disponible(agenda, servidor):
    _control(servidor, latencia_ms=1200)
    inicio = time.monotonic()
    with pytest.raises(ServicioNoDisponible):
        _confirmar(agenda)
    assert time.monotonic() - inicio < 1.0  # no espera la respuesta lenta


def test_el_simulado_exige_token_de_servicio_valido(servidor):
    for token in (None, "1.abc", servicio_token.firmar(b"otro", "/reservas")):
        cabeceras = {"Content-Type": "application/json", **({"X-Servicio-Token": token} if token else {})}
        peticion = urllib.request.Request(servidor + "/reservas", b"{}", method="POST", headers=cabeceras)
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(peticion, timeout=2)
        assert error.value.code == 401


def test_el_simulado_valida_el_esquema_y_no_acepta_cedulas(servidor):
    firma = servicio_token.firmar(SECRETO, "/reservas")
    cuerpo = json.dumps({"turno_ref": _ref(), "franja_ref": _ref(), "afiliado_ref": "1750000018", "clave": _ref(), "extra": 1}).encode()
    peticion = urllib.request.Request(servidor + "/reservas", cuerpo, method="POST", headers={"X-Servicio-Token": firma})
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(peticion, timeout=2)
    assert error.value.code == 422  # campo desconocido: esquema cerrado


def test_cancelar_en_la_agenda(agenda):
    c = agenda.confirmar(turno_ref="turno1", franja_ref=_ref(), afiliado_ref=_ref(), clave=_ref())
    assert c.estado == "CONFIRMADA"
    agenda.cancelar(turno_ref="turno1")
    with pytest.raises(ServicioNoDisponible):
        agenda.cancelar(turno_ref="../../etc/passwd")  # la referencia se valida antes de armar la ruta


def test_circuito_abierto_tras_5_fallos_y_no_llama_mas_al_tercero(servidor, parametro):
    parametro("integracion_reintentos", 0)
    _control(servidor, modo="caido")
    agenda = AgendaHttp(servidor, SECRETO, dormir=lambda s: None)
    for _ in range(5):
        with pytest.raises(ServicioNoDisponible):
            _confirmar(agenda)
    with pytest.raises(ServicioNoDisponible, match="circuito abierto"):
        _confirmar(agenda)


def test_reintentos_con_espera_creciente(servidor, parametro):
    parametro("integracion_espera_base_segundos", 0.5)
    esperas = []
    _control(servidor, modo="caido")
    agenda = AgendaHttp(servidor, SECRETO, dormir=esperas.append)
    with pytest.raises(ServicioNoDisponible):
        _confirmar(agenda)
    assert esperas == [0.5, 1.0]


def test_la_agenda_memoria_es_idempotente_y_se_puede_romper_a_proposito():
    a = AgendaMemoria()
    clave = _ref()
    assert a.confirmar(turno_ref="t", franja_ref="f", afiliado_ref="a", clave=clave) is a.confirmar(turno_ref="t", franja_ref="f", afiliado_ref="a", clave=clave)
    a.caida = True
    with pytest.raises(ServicioNoDisponible):
        a.confirmar(turno_ref="t", franja_ref="f", afiliado_ref="a", clave=_ref())


def test_a_la_agenda_solo_se_envian_identificadores_opacos_nunca_cedulas(oferta, ciudadano):
    """Lo que el portal manda a la agenda (franja e identificador interno del afiliado): sin cédula ni nombres (§4.6)."""
    import uuid as _u

    from apps.integraciones import servicios as integraciones
    from apps.turnos import servicios
    from tests.ayudas import franja_futura

    memoria = integraciones.agenda_memoria()
    llamadas = []
    original = memoria.confirmar
    memoria.confirmar = lambda **kw: (llamadas.append(kw), original(**kw))[1]
    try:
        usuario = ciudadano(nombres="Nombre", apellidos="Visible")
        servicios.reservar(solicitante=usuario, franja_id=franja_futura(oferta["est1"], oferta["esp1"]).id, motivo="", clave_idempotencia=_u.uuid4().hex)
    finally:
        memoria.confirmar = original
    (llamada,) = llamadas
    assert set(llamada) == {"turno_ref", "franja_ref", "afiliado_ref", "clave"}
    assert usuario.cedula not in json.dumps(llamada, default=str) and "Nombre" not in json.dumps(llamada, default=str)

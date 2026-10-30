"""Adaptador HTTP de afiliación (R07, RT-10, CA-15): token de servicio, tiempo de espera, reintentos, circuito, esquema."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from apps.integraciones import servicio_token
from apps.integraciones.adaptadores.http import AfiliacionHttp
from apps.integraciones.circuito import Circuito
from apps.integraciones.puertos import ServicioNoDisponible
from config.cedula import sintetica

SECRETO = b"secreto-de-servicio-de-prueba"
CEDULA = sintetica(5)
BUENA = {"cedula": CEDULA, "nombres": "Ana", "apellidos": "Prueba", "correo": "a@correo.iess-prueba.example",
         "telefono": "0990000000", "vigente": True, "fecha_nacimiento": "1990-05-04"}


class _Servidor:
    """Servicio de afiliación de prueba con comportamiento configurable."""

    def __init__(self):
        self.modo = "ok"
        self.peticiones = 0
        self.tokens_validos = 0
        servidor = self

        class Manejador(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                servidor.peticiones += 1
                if servicio_token.verificar(SECRETO, self.headers.get("X-Servicio-Token"), self.path):
                    servidor.tokens_validos += 1
                else:
                    self.send_response(401)
                    self.end_headers()
                    return
                if servidor.modo == "lento":
                    time.sleep(1.0)
                if servidor.modo == "error":
                    self.send_response(503)
                    self.end_headers()
                    return
                if servidor.modo == "no_existe":
                    self.send_response(404)
                    self.end_headers()
                    return
                cuerpo = {"ok": BUENA, "esquema": {"cedula": CEDULA, "vigente": "si"},
                          "otra_cedula": {**BUENA, "cedula": sintetica(6)},
                          "vigente_no_booleano": {**BUENA, "vigente": "true"}}.get(servidor.modo, BUENA)
                crudo = json.dumps(cuerpo).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(crudo)))
                self.end_headers()
                self.wfile.write(crudo)

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Manejador)
        self.url = f"http://127.0.0.1:{self.http.server_address[1]}"
        threading.Thread(target=self.http.serve_forever, daemon=True).start()

    def cerrar(self):
        self.http.shutdown()


@pytest.fixture
def servidor():
    s = _Servidor()
    yield s
    s.cerrar()


@pytest.fixture
def adaptador(servidor, parametro):
    parametro("integracion_timeout_segundos", 0.4)
    parametro("integracion_espera_base_segundos", 0.0)
    return AfiliacionHttp(servidor.url, SECRETO, dormir=lambda s: None)


def test_consulta_correcta_valida_y_devuelve_datos(adaptador, servidor):
    datos = adaptador.consultar(CEDULA)
    assert datos.cedula == CEDULA and datos.vigente is True and str(datos.fecha_nacimiento) == "1990-05-04"
    assert servidor.tokens_validos == 1  # se autenticó con token de servicio firmado


def test_sin_token_de_servicio_el_tercero_rechaza(servidor, parametro):
    parametro("integracion_reintentos", 0)
    adaptador = AfiliacionHttp(servidor.url, b"otro-secreto", dormir=lambda s: None)
    with pytest.raises(ServicioNoDisponible):
        adaptador.consultar(CEDULA)


def test_el_token_de_servicio_vence_y_esta_ligado_a_la_ruta():
    token = servicio_token.firmar(SECRETO, "/afiliados/1", ahora=1000)
    assert servicio_token.verificar(SECRETO, token, "/afiliados/1", ahora=1030)
    assert not servicio_token.verificar(SECRETO, token, "/afiliados/2", ahora=1030)  # otra ruta
    assert not servicio_token.verificar(SECRETO, token, "/afiliados/1", ahora=1100)  # vencido (60 s)
    assert not servicio_token.verificar(b"", token, "/afiliados/1", ahora=1030)


def test_cedula_no_afiliada_devuelve_none_y_no_es_un_fallo(adaptador, servidor):
    servidor.modo = "no_existe"
    assert adaptador.consultar(CEDULA) is None
    assert not adaptador.circuito.abierto()


def test_cedula_con_formato_invalido_no_llega_al_tercero(adaptador, servidor):
    assert adaptador.consultar("no-valida") is None
    assert servidor.peticiones == 0


def test_T07_RT10_error_5xx_es_servicio_no_disponible_nunca_cobertura(adaptador, servidor, parametro):
    servidor.modo = "error"
    with pytest.raises(ServicioNoDisponible):
        adaptador.consultar(CEDULA)
    assert servidor.peticiones == 1 + 2  # un intento y 2 reintentos (Anexo E)


def test_CA15_tiempo_de_espera_agotado_es_servicio_no_disponible(adaptador, servidor, parametro):
    parametro("integracion_reintentos", 0)
    servidor.modo = "lento"  # responde en 1 s; el límite es 0,4 s
    inicio = time.monotonic()
    with pytest.raises(ServicioNoDisponible):
        adaptador.consultar(CEDULA)
    assert time.monotonic() - inicio < 0.9


@pytest.mark.parametrize("modo", ["esquema", "otra_cedula", "vigente_no_booleano"])
def test_respuestas_mal_formadas_se_tratan_como_fallo_no_como_dato(adaptador, servidor, modo):
    servidor.modo = modo
    with pytest.raises(ServicioNoDisponible):
        adaptador.consultar(CEDULA)


def test_los_reintentos_usan_espera_creciente(servidor, parametro):
    parametro("integracion_espera_base_segundos", 0.5)
    esperas = []
    servidor.modo = "error"
    adaptador = AfiliacionHttp(servidor.url, SECRETO, dormir=esperas.append)
    with pytest.raises(ServicioNoDisponible):
        adaptador.consultar(CEDULA)
    assert esperas == [0.5, 1.0]


def test_CA15_circuito_abierto_tras_5_fallos_y_deja_de_llamar_al_tercero(servidor, parametro):
    parametro("integracion_reintentos", 0)
    servidor.modo = "error"
    adaptador = AfiliacionHttp(servidor.url, SECRETO, dormir=lambda s: None)
    for _ in range(5):
        with pytest.raises(ServicioNoDisponible):
            adaptador.consultar(CEDULA)
    llamadas = servidor.peticiones
    with pytest.raises(ServicioNoDisponible, match="circuito abierto"):
        adaptador.consultar(CEDULA)
    assert servidor.peticiones == llamadas  # ni una petición más mientras está abierto


def test_el_circuito_se_cierra_tras_la_pausa_y_un_exito_limpia_los_fallos():
    ahora = [0.0]
    circuito = Circuito(fallos=5, ventana_segundos=30, pausa_segundos=30, reloj=lambda: ahora[0])
    for _ in range(5):
        circuito.registrar_fallo()
    assert circuito.abierto()
    ahora[0] = 31
    assert not circuito.abierto()
    for _ in range(4):
        circuito.registrar_fallo()
    circuito.registrar_exito()
    circuito.registrar_fallo()
    assert not circuito.abierto()  # los 4 anteriores se olvidaron


def test_los_fallos_fuera_de_la_ventana_de_30_s_no_abren_el_circuito():
    ahora = [0.0]
    circuito = Circuito(fallos=5, ventana_segundos=30, pausa_segundos=30, reloj=lambda: ahora[0])
    for i in range(10):
        ahora[0] = i * 10  # un fallo cada 10 s: nunca hay 5 dentro de 30 s
        circuito.registrar_fallo()
    assert not circuito.abierto()


@pytest.mark.django_db
def test_vigencia_se_cachea_15_minutos_y_solo_guarda_un_booleano(afiliar, settings):
    from django.core.cache import caches

    from apps.integraciones import servicios

    datos = afiliar()
    assert servicios.consultar_vigencia(datos.cedula) is True
    assert servicios.consultar_vigencia(datos.cedula) is True
    assert servicios.afiliacion_memoria().consultas == 1  # la segunda salió de la caché
    valores = [v for k, v in caches["default"]._cache.items() if "afiliacion" in k]
    assert valores, "no se guardó nada en la caché"
    assert datos.correo not in str(caches["default"]._cache) and datos.nombres not in str(caches["default"]._cache)


@pytest.mark.django_db
def test_si_el_servicio_cae_y_no_hay_cache_no_se_asume_cobertura(afiliar):
    from apps.integraciones import servicios

    datos = afiliar()
    servicios.afiliacion_memoria().caido = True
    with pytest.raises(servicios.ServicioNoDisponible):
        servicios.consultar_vigencia(datos.cedula)

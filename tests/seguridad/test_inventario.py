"""API9 / Anexo D: una ruta que no está en el inventario no se publica. Y el inventario real (docs/endpoints.md) no se desfasa."""
import re
from pathlib import Path

import pytest
from django.conf import settings
from django.test import Client
from django.urls import URLPattern, URLResolver, get_resolver

from config.inventario import ENTRADAS, INVENTARIO
from config.inventario_docs import generar

RAIZ = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.django_db


def _normalizar(ruta: str) -> str:
    ruta = re.sub(r"<uuid:\w+>", "{id}", ruta)
    ruta = re.sub(r"<(?:cedula|token):(\w+)>", r"{\1}", ruta)
    ruta = re.sub(r"<\w+:(\w+)>", r"{\1}", ruta)
    return "/" + ruta.lstrip("/")


def _rutas(urlconf=None):
    """[(ruta normalizada, {método: id de endpoint} o None si la vista no está en el inventario, nombre)]."""
    encontradas = []

    def recorrer(patrones, prefijo):
        for p in patrones:
            if isinstance(p, URLResolver):
                recorrer(p.url_patterns, prefijo + str(p.pattern))
            elif isinstance(p, URLPattern):
                vista = p.callback
                if hasattr(vista, "endpoints"):
                    ids = dict(vista.endpoints)
                elif hasattr(vista, "endpoint_id"):
                    ids = {m: vista.endpoint_id for m in vista.endpoint_metodos}
                else:
                    ids = None
                encontradas.append((_normalizar(prefijo + str(p.pattern)), ids, p.name))

    recorrer(get_resolver(urlconf).url_patterns, "")
    return encontradas


def test_toda_ruta_publicada_esta_en_el_inventario():
    sin_inventario = [ruta for ruta, ids, _ in _rutas() if not ids]
    assert not sin_inventario, f"Rutas fuera del inventario (Anexo D): {sin_inventario}"


def test_las_rutas_coinciden_exactamente_con_el_inventario():
    reales = {(metodo, ruta, id_) for ruta, ids, _ in _rutas() for metodo, id_ in (ids or {}).items()}
    declaradas = {(metodo, e.ruta, e.id) for e in ENTRADAS for metodo in e.metodos}
    assert reales == declaradas, {"solo_en_codigo": reales - declaradas, "solo_en_inventario": declaradas - reales}


def test_el_inventario_tiene_E01_a_E21_sin_huecos_ni_repetidos():
    ids = [e.id for e in ENTRADAS if not e.soporte]
    assert ids == [f"E{n:02d}" for n in range(1, 22)] and len({e.id for e in ENTRADAS}) == len(ENTRADAS)


def test_META_una_ruta_agregada_sin_inventario_se_detecta():
    """Prueba de la prueba: una URLconf con una ruta de depuración extra debe aparecer como violación."""
    sin_inventario = [ruta for ruta, ids, _ in _rutas("tests.seguridad.urls_inseguras") if not ids]
    assert sin_inventario == ["/debug/volcado"]


def test_docs_endpoints_md_esta_generado_desde_el_inventario():
    actual = (RAIZ / "docs" / "endpoints.md").read_text(encoding="utf-8")
    assert actual == generar(), "docs/endpoints.md está desactualizado: ejecute `python manage.py generar_inventario`"


def test_el_inventario_documenta_para_cada_endpoint_autenticacion_autorizacion_y_csrf():
    for e in ENTRADAS:
        assert e.funcion and e.autenticacion and e.autorizacion and e.limites_texto is not None and e.idempotencia_csrf, e.id
        if set(e.metodos) & {"POST", "DELETE"}:
            assert "CSRF" in e.idempotencia_csrf, f"{e.id} cambia estado y debe exigir token CSRF"


@pytest.mark.parametrize("id_", ["E05", "E09", "E10", "E11", "E12", "E16"])
def test_toda_ruta_que_cambia_estado_rechaza_GET(id_):
    ruta = INVENTARIO[id_].ruta.replace("{id}", "00000000-0000-0000-0000-000000000000")
    assert Client().get(ruta, secure=True).status_code == 405


def test_las_rutas_de_estado_no_responden_a_metodos_inesperados():
    for metodo in ("put", "patch", "delete", "head", "trace"):
        r = getattr(Client(), metodo)("/turnos", secure=True)
        assert r.status_code in (405, 302, 301), (metodo, r.status_code)


def test_CORS_cerrado_ninguna_respuesta_lo_abre_y_no_hay_middleware_de_cors():
    assert not any("cors" in m.lower() for m in settings.MIDDLEWARE) and not any("cors" in a.lower() for a in settings.INSTALLED_APPS)
    for ruta in ("/disponibilidad", "/salud", "/sesion"):
        r = Client().get(ruta, HTTP_ORIGIN="https://atacante.example", secure=True)
        assert "Access-Control-Allow-Origin" not in r
    r = Client().options("/turnos", HTTP_ORIGIN="https://atacante.example", HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST", secure=True)
    assert "Access-Control-Allow-Origin" not in r


def test_no_hay_rutas_de_prueba_ni_de_depuracion_en_el_proyecto():
    for ruta, _, nombre in _rutas():
        assert not re.search(r"debug|test|prueba|admin|swagger|openapi|graphql|phpinfo", ruta, re.I), ruta
    assert Client().get("/admin/", secure=True).status_code == 404 and Client().get("/__debug__/", secure=True).status_code == 404

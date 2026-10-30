"""Pruebas del §10.1 del informe que no tenían un hogar natural: T01, T10, T12 y T15 (nombradas con su identificador para la trazabilidad)."""
import json
import re
import uuid
from pathlib import Path

import django
import pytest
from django.apps import apps as registro
from django.db import connection
from django.urls import get_resolver

from apps.integraciones import servicios as integraciones
from apps.integraciones import servicio_token
from tests.ayudas import franja_futura

RAIZ = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.django_db

# T01: datos que NO se recolectan (I03 laboral, I04 salud/agenda clínica, I05 religioso).
PROHIBIDOS = ("religi", "laboral", "empleador", "ocupacion", "salario", "diagnost", "enfermedad", "patolog", "medicament", "historia_clinica", "discapacidad", "etnia", "creencia")


def test_T01_el_esquema_no_tiene_campos_laborales_religiosos_ni_de_salud():
    campos = [f"{m._meta.label}.{f.name}" for m in registro.get_models() for f in m._meta.get_fields() if hasattr(f, "column")]
    with connection.cursor() as c:
        c.execute("select table_name || '.' || column_name from information_schema.columns where table_schema = 'public'")
        columnas = [fila[0] for fila in c.fetchall()]
    assert campos and columnas
    hallados = [x for x in campos + columnas if any(p in x.lower() for p in PROHIBIDOS)]
    assert hallados == [], hallados


def test_T01_la_reserva_se_completa_sin_ningun_dato_laboral_religioso_ni_de_salud(oferta, ciudadano):
    from apps.turnos import servicios

    turno, creado = servicios.reservar(solicitante=ciudadano(), franja_id=franja_futura(oferta["est1"], oferta["esp1"]).id, motivo="CONTROL", clave_idempotencia=uuid.uuid4().hex)
    assert creado and turno.estado in {"RESERVADO", "PENDIENTE", "CONFIRMADO"}


def test_T10_la_respuesta_de_afiliacion_es_minima_sin_datos_de_salud_ni_laborales():
    campos = {f for f in integraciones.DatosAfiliacion.__dataclass_fields__}
    assert campos == {"cedula", "nombres", "apellidos", "correo", "telefono", "vigente", "fecha_nacimiento"}  # nada más cruza la frontera


def test_T10_no_existe_ninguna_ruta_de_exportacion_masiva_ni_para_entrenar_ia():
    rutas = []

    def recorrer(patrones, prefijo=""):
        for p in patrones:
            texto = prefijo + str(p.pattern)
            if hasattr(p, "url_patterns"):
                recorrer(p.url_patterns, texto)
            else:
                rutas.append(texto.lower())

    recorrer(get_resolver().url_patterns)
    assert rutas
    assert not [r for r in rutas if re.search(r"(masiv|entren|dataset|bulk|volcado|dump|\bia\b|machine|ml/)", r)], rutas
    # Lo único exportable es puntual, con doble aprobación y con tope de filas (parámetros del Anexo E).
    exportaciones = [r for r in rutas if "exportacion" in r]
    assert exportaciones and all("aprobar" in r or "descargar" in r or r.endswith("exportaciones") for r in exportaciones)


def test_T10_el_acceso_del_tercero_es_revocable_cambiando_su_clave_de_servicio():
    clave_vieja, clave_nueva = b"clave-de-servicio-vieja-" * 2, b"clave-de-servicio-nueva-" * 2
    token = servicio_token.firmar(clave_vieja, "/afiliados/1750000018")
    assert servicio_token.verificar(clave_vieja, token, "/afiliados/1750000018")
    assert not servicio_token.verificar(clave_nueva, token, "/afiliados/1750000018")  # rotada la clave, el acceso anterior muere
    assert not servicio_token.verificar(clave_vieja, token, "/afiliados/otra-ruta")  # y el token no sirve para otra ruta


def test_T12_el_plan_de_respuesta_define_responsables_plazos_formato_y_simulacros():
    texto = (RAIZ / "docs" / "respuesta_incidentes.md").read_text(encoding="utf-8").lower()
    for clave in ("responsables", "72 h", "formato de notificación", "simulacros", "phishing", "ejercicio de mesa", "rpo", "rto"):
        assert clave in texto, clave


def test_T12_ningun_mensaje_pide_la_clave_ni_un_codigo_por_correo_o_sms():
    from apps.notificaciones import servicios as notificaciones

    plantillas = notificaciones.PLANTILLAS
    for nombre, texto in plantillas.items():
        cuerpo = " ".join(texto).lower()
        assert not re.search(r"(env[ií]e|ingrese|escriba|responda).{0,40}(clave|contrase[ñn]a)", cuerpo), nombre


def test_T15_django_esta_en_la_linea_5_2_LTS_y_todo_esta_fijado_con_hashes():
    assert django.VERSION[:2] == (5, 2)
    for nombre in ("requirements.txt", "requirements-dev.txt"):
        texto = (RAIZ / nombre).read_text(encoding="utf-8")
        bloques = re.findall(r"^([A-Za-z0-9_.\-]+)==([^\s\\]+)", texto, re.M)
        assert bloques, nombre
        for paquete, _ in bloques:
            tramo = re.search(rf"^{re.escape(paquete)}==.*?(?=^\S|\Z)", texto, re.M | re.S).group(0)
            assert "--hash=sha256:" in tramo, f"{paquete} sin hash en {nombre}"
    assert re.search(r"^django==5\.2\.\d+", (RAIZ / "requirements.txt").read_text(encoding="utf-8"), re.M | re.I)


def test_T15_la_ultima_verificacion_de_dependencias_no_tiene_criticos_ni_altos():
    evidencias = sorted((RAIZ / "docs" / "evidencia").glob("pip_audit_*.json"))
    if not evidencias:
        pytest.skip("aún no se ejecutó scripts/verify.sh")
    datos = json.loads(evidencias[-1].read_text(encoding="utf-8"))
    vulnerables = [d for d in datos.get("dependencies", []) if d.get("vulns")]
    assert vulnerables == [], [d["name"] for d in vulnerables]


def test_T15_existe_un_SBOM_CycloneDX_valido_de_la_ultima_verificacion():
    sboms = sorted((RAIZ / "docs" / "evidencia").glob("sbom_*.json"))
    if not sboms:
        pytest.skip("aún no se ejecutó scripts/verify.sh")
    datos = json.loads(sboms[-1].read_text(encoding="utf-8"))
    assert datos["bomFormat"] == "CycloneDX" and any(c["name"].lower() == "django" for c in datos["components"])


def test_T09_la_ultima_prueba_de_carga_cumple_las_metas_del_informe():
    evidencias = sorted((RAIZ / "docs" / "evidencia").glob("carga_*.json"))
    if not evidencias:
        pytest.skip("aún no se ejecutó scripts/carga.sh")
    ev = json.loads(evidencias[-1].read_text(encoding="utf-8"))
    for nombre in ("sostenida", "rafaga"):
        esc = ev["escenarios"][nombre]
        assert esc["p95_ms"] <= ev["metas"]["p95_ms"] == 2000.0 and esc["tasa_errores"] < ev["metas"]["errores"] == 0.01, (nombre, esc)
        assert esc["solicitudes"] > 0
    assert ev["origen_abusivo_limitado"]["cumple"] and ev["los_limites_no_bloquean_a_todos"]  # el abusador recibe 429; los demás, 200


def test_T09_el_contador_atomico_de_redis_cuenta_y_caduca(settings):
    """Camino real de Redis del limitador y las métricas (en las pruebas normales la caché es de memoria)."""
    import os

    import redis
    from django.core.cache import caches
    from django.test import override_settings

    from config import limites

    host = os.environ.get("REDIS_HOST")
    if not host or not os.environ.get("REDIS_CLAVE"):
        pytest.skip("sin Redis en este entorno")
    destino = f"redis://:{os.environ['REDIS_CLAVE']}@{host}:6379/9"
    try:
        redis.Redis.from_url(destino).ping()
    except redis.RedisError:
        pytest.skip("Redis no disponible")
    with override_settings(CACHES={"limites": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": destino}}):
        caches["limites"].clear()
        clave = f"t09:{uuid.uuid4().hex}"
        assert limites.incrementar("limites", [clave, clave + "b"], 5) == [1, 1]
        assert limites.incrementar("limites", [clave], 5) == [2]
        cliente = redis.Redis.from_url(destino)
        assert 0 < cliente.ttl(caches["limites"].make_key(clave)) <= 5  # la caducidad se fija una sola vez
        caches["limites"].clear()


def test_T14_el_ultimo_analisis_de_ZAP_no_tiene_hallazgos_altos_ni_medios_sin_justificar():
    evidencias = sorted((RAIZ / "docs" / "evidencia").glob("zap_resumen_*.json"))
    if not evidencias:
        pytest.skip("aún no se ejecutó scripts/zap.sh")
    resumen = json.loads(evidencias[-1].read_text(encoding="utf-8"))
    assert resumen["conteo"]["alto"] == 0 and resumen["medios_sin_justificar"] == [] and resumen["cumple"] is True


def test_T14_cada_IGNORE_de_ZAP_lleva_justificacion():
    lineas = [x for x in (RAIZ / "scripts" / "zap_reglas.conf").read_text(encoding="utf-8").splitlines() if x and not x.startswith("#")]
    assert lineas and all(re.match(r"^\d+\tIGNORE\t\(.{20,}", x) for x in lineas), lineas

"""Higiene del repositorio y del despliegue: sin secretos, redes por zonas, sin puertos de datos en el host (R08, R09, Anexo J)."""
import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
EXCLUIDOS = {".git", "var", "staticfiles", "__pycache__", ".pytest_cache", "docs", "prompts"}


def _archivos_de_texto():
    """Archivos de texto del repositorio (se omiten binarios, el .env local, claves de desarrollo y datos de la lista)."""
    for ruta in RAIZ.rglob("*"):
        partes = set(ruta.relative_to(RAIZ).parts)
        if not ruta.is_file() or partes & EXCLUIDOS or ruta.name == ".env":
            continue
        if ruta.suffix in {".pem", ".key", ".pdf", ".pyc"} or ruta.name == "claves_filtradas.txt":
            continue
        try:
            yield ruta, ruta.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue


def test_el_env_real_no_se_versiona_y_el_ejemplo_no_trae_valores():
    ignorado = (RAIZ / ".gitignore").read_text(encoding="utf-8")
    assert re.search(r"^\.env$", ignorado, re.M) and "*.pem" in ignorado and "*.key" in ignorado
    for linea in (RAIZ / ".env.example").read_text(encoding="utf-8").splitlines():
        if linea and not linea.startswith("#"):
            nombre, _, valor = linea.partition("=")
            assert valor == "", f".env.example no debe traer valores: {nombre}"


_PATRONES_SECRETOS = [
    (r"-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----", "clave privada"),
    (r"(?i)\b(secret_key|password|passwd|api[_-]?key|token)\b\s*[:=]\s*[\"'][A-Za-z0-9+/=_\-]{24,}[\"']", "secreto literal"),
    (r"AKIA[0-9A-Z]{16}", "credencial de nube"),
    (r"(?i)postgres(ql)?://[^:\s]+:[^@\s]{4,}@", "URL de base con contraseña"),
    (r"\bgh[pousr]_[A-Za-z0-9]{36,}\b", "token de GitHub"),
]


def test_ningun_secreto_literal_en_el_codigo_ni_la_configuracion():
    hallazgos = []
    for ruta, texto in _archivos_de_texto():
        if "tests" in ruta.parts:
            continue
        for patron, que in _PATRONES_SECRETOS:
            if re.search(patron, texto):
                hallazgos.append(f"{ruta.relative_to(RAIZ)}: {que}")
    assert not hallazgos, hallazgos


def test_el_escaner_de_secretos_si_detecta_secretos(tmp_path):
    """Prueba de la prueba."""
    for texto in ('SECRET_KEY = "abcdefghijklmnopqrstuvwxyz0123456789"', "-----BEGIN PRIVATE KEY-----", "postgres://usuario:clavesecreta@host/db"):
        assert any(re.search(p, texto) for p, _ in _PATRONES_SECRETOS), texto


def _servicios_del_compose() -> dict[str, str]:
    texto = (RAIZ / "docker-compose.yml").read_text(encoding="utf-8")
    cuerpo = texto.split("\nservices:\n", 1)[1]
    bloques = re.split(r"\n  (?=[a-z_]+:\n)", "\n" + cuerpo)
    return {m.group(1): b for b in bloques if (m := re.match(r"\n?([a-z_]+):\n", b if b.startswith("\n") else "\n" + b))}


def test_el_compose_define_las_zonas_de_confianza_de_la_figura_3():
    texto = (RAIZ / "docker-compose.yml").read_text(encoding="utf-8")
    redes = re.search(r"\nnetworks:\n((?:  .*\n|\n)+?)\nvolumes:", texto).group(1)
    for red in ("perimetro", "aplicacion", "datos", "externos"):
        assert re.search(rf"^  {red}:", redes, re.M), red
    # Las redes de aplicación, datos y externos son internas (sin salida a Internet).
    for red in ("aplicacion", "datos", "externos"):
        assert re.search(rf"^  {red}:\n    internal: true", redes, re.M), red


def test_la_base_de_datos_y_redis_no_publican_puertos_en_el_host():
    servicios = _servicios_del_compose()
    for nombre in ("db", "redis", "app", "afiliacion_sim", "migrar"):
        assert "ports:" not in servicios[nombre], f"{nombre} no debe exponer puertos"
    assert "ports:" in servicios["proxy"]  # el proxy es el único punto de entrada


def test_la_base_y_redis_solo_estan_en_la_red_de_datos():
    servicios = _servicios_del_compose()
    assert re.search(r"networks: \[datos\]", servicios["db"]) and re.search(r"networks: \[datos\]", servicios["redis"])
    assert "datos" not in servicios["proxy"] and "externos" not in servicios["proxy"]  # el perímetro no ve datos ni terceros
    assert re.search(r"networks: \[externos\]", servicios["afiliacion_sim"])


def test_el_compose_no_contiene_secretos_literales_solo_variables():
    texto = (RAIZ / "docker-compose.yml").read_text(encoding="utf-8")
    for linea in texto.splitlines():
        if re.search(r"(?i)(PASSWORD|CLAVE|SECRET|TOKEN)\w*:\s*\S", linea) and "${" not in linea and "<<" not in linea:
            assert linea.strip().startswith("#") or "_CLAVE: \"\"" in linea, f"secreto literal en el compose: {linea}"


def test_el_dockerfile_final_usa_usuario_no_root_healthcheck_y_sin_pip():
    texto = (RAIZ / "Dockerfile").read_text(encoding="utf-8")
    final = texto.split("AS final", 1)[1]
    assert re.search(r"^USER app$", final, re.M) and "HEALTHCHECK" in final
    assert "rm -rf /opt/venv/lib/python3.13/site-packages/pip" in final  # sin herramientas innecesarias
    assert "--require-hashes" in texto  # dependencias verificadas por hash


def test_las_dependencias_estan_fijadas_con_hashes():
    for nombre in ("requirements.txt", "requirements-dev.txt"):
        texto = (RAIZ / nombre).read_text(encoding="utf-8")
        paquetes = re.findall(r"^([A-Za-z0-9_.\-]+)==", texto, re.M)
        assert paquetes and texto.count("--hash=sha256:") >= len(paquetes), nombre


def test_postgresql_18_y_python_313():
    compose = (RAIZ / "docker-compose.yml").read_text(encoding="utf-8")
    assert "image: postgres:18" in compose
    assert "python:3.13" in (RAIZ / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^django==5\.2\.\d+", (RAIZ / "requirements.txt").read_text(encoding="utf-8"), re.M)


def test_sin_django_gis():
    for ruta, texto in _archivos_de_texto():
        if ruta.suffix == ".py" and "tests" not in ruta.parts:
            assert "django.contrib.gis" not in texto, ruta


def test_T18_los_servicios_estan_endurecidos_sin_root_solo_lectura_sin_capacidades_y_con_limites():
    servicios = _servicios_del_compose()
    for nombre in ("proxy", "app", "tareas", "migrar", "afiliacion_sim", "agenda_sim", "redis", "db"):
        b = servicios[nombre]
        assert "<<: *endurecido" in b or ("read_only: true" in b and "cap_drop: [ALL]" in b), f"{nombre}: sin endurecimiento"
        assert "mem_limit:" in b and "cpus:" in b, f"{nombre}: sin límites de recursos"
        assert "healthcheck:" in b or nombre in {"migrar", "app"}, nombre  # `migrar` termina solo; `app` lo declara en el Dockerfile (HEALTHCHECK)
        assert not re.search(r"^\s+privileged:\s*true", b, re.M) and "network_mode: host" not in b and "/var/run/docker.sock" not in b, nombre
    anclas = re.search(r"x-endurecido: &endurecido\n((?:  .*\n)+)", (RAIZ / "docker-compose.yml").read_text(encoding="utf-8")).group(1)
    for exigido in ("read_only: true", "cap_drop: [ALL]", "no-new-privileges:true", "pids_limit"):
        assert exigido in anclas, exigido
    assert "cap_add" not in servicios["app"] and "cap_add" not in servicios["proxy"]  # solo la base necesita cinco, por su arranque como root


def test_T18_las_imagenes_tienen_version_y_resumen_fijados():
    compose = (RAIZ / "docker-compose.yml").read_text(encoding="utf-8")
    imagenes = re.findall(r"^\s+image: (\S+)", compose, re.M)
    assert imagenes and all(re.search(r":\d[\w.\-]*@sha256:[0-9a-f]{64}$", i) for i in imagenes), imagenes  # nada de latest ni de etiquetas móviles
    externas = [f for f in re.findall(r"^FROM (\S+)", (RAIZ / "Dockerfile").read_text(encoding="utf-8"), re.M) if ":" in f]
    assert externas and all("@sha256:" in f for f in externas), externas


def test_T18_las_aplicaciones_corren_como_usuario_no_root():
    dockerfile = (RAIZ / "Dockerfile").read_text(encoding="utf-8")
    final = dockerfile.split("AS final", 1)[1]
    assert re.search(r"^USER app$", final, re.M) and "useradd --system --uid 10001" in final

"""Prueba de arquitectura (obligatoria en las 4 etapas): si una regla se rompe, esta prueba FALLA.

Dos niveles: (1) import-linter con los contratos de `.importlinter`; (2) inspección de AST para lo que import-linter
no expresa (ORM de escritura, permisos sueltos, SQL sobre la auditoría, plantillas con lógica). Además hay pruebas de
las pruebas: cada regla se ejercita contra código que la viola, para demostrar que de verdad detecta.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from . import reglas
from .reglas import APPS, RAIZ

CAPA_DE_VISTAS = ("vistas.py", "esquemas.py", "urls.py")


def _archivos(nombre: str):
    return sorted(p for p in reglas.modulos_de_produccion() if p.name == nombre)


def _app_de(ruta: Path) -> str | None:
    partes = ruta.relative_to(RAIZ).parts
    return partes[1] if partes[0] == "apps" else None


def _lint_imports(cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["lint-imports"], cwd=cwd, capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(cwd)}
    )


# --- Nivel 1: import-linter ------------------------------------------------------------------------------------------------------


def test_import_linter_todos_los_contratos_se_cumplen():
    resultado = _lint_imports(RAIZ)
    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
    assert "0 broken" in resultado.stdout


def test_import_linter_detecta_una_vista_que_importa_modelos(tmp_path):
    """Prueba de la prueba: se copia el código, se inyecta una violación y el contrato debe romperse."""
    copia = tmp_path / "proyecto"
    for carpeta in ("apps", "config"):
        shutil.copytree(RAIZ / carpeta, copia / carpeta, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(RAIZ / ".importlinter", copia / ".importlinter")
    with open(copia / "apps/cuentas/vistas.py", "a", encoding="utf-8") as f:
        f.write("\nfrom apps.cuentas.modelos import Usuario  # VIOLACIÓN INYECTADA\n")
    resultado = _lint_imports(copia)
    assert resultado.returncode != 0 and "BROKEN" in resultado.stdout


def test_import_linter_detecta_que_otra_app_importe_los_modelos_de_auditoria(tmp_path):
    copia = tmp_path / "proyecto"
    for carpeta in ("apps", "config"):
        shutil.copytree(RAIZ / carpeta, copia / carpeta, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(RAIZ / ".importlinter", copia / ".importlinter")
    with open(copia / "apps/turnos/servicios.py", "a", encoding="utf-8") as f:
        f.write("\nfrom apps.auditoria.modelos import EventoAuditoria  # VIOLACIÓN INYECTADA\n")
    resultado = _lint_imports(copia)
    assert resultado.returncode != 0 and "BROKEN" in resultado.stdout


def test_import_linter_detecta_una_dependencia_en_sentido_contrario(tmp_path):
    copia = tmp_path / "proyecto"
    for carpeta in ("apps", "config"):
        shutil.copytree(RAIZ / carpeta, copia / carpeta, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(RAIZ / ".importlinter", copia / ".importlinter")
    with open(copia / "apps/auditoria/servicios.py", "a", encoding="utf-8") as f:
        f.write("\nfrom apps.cuentas import servicios as _cuentas  # VIOLACIÓN INYECTADA (auditoria no depende de cuentas)\n")
    resultado = _lint_imports(copia)
    assert resultado.returncode != 0 and "BROKEN" in resultado.stdout


# --- Nivel 2: inspección de AST ---------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("nombre", CAPA_DE_VISTAS)
def test_las_vistas_son_finas_sin_orm_ni_permisos_propios(nombre):
    archivos = _archivos(nombre)
    assert archivos or nombre != "vistas.py"
    problemas = {str(a.relative_to(RAIZ)): reglas.violaciones_vista_fina(a) for a in archivos}
    assert not any(problemas.values()), problemas


def test_las_escrituras_al_orm_solo_ocurren_en_servicios():
    problemas = {str(p.relative_to(RAIZ)): v for p in reglas.modulos_de_produccion() if (v := reglas.violaciones_escritura_fuera_de_servicios(p))}
    assert not problemas, problemas


def test_las_apps_solo_se_importan_entre_si_por_servicios():
    problemas = {}
    for ruta in reglas.modulos_de_produccion():
        app = _app_de(ruta)
        if app and (v := reglas.violaciones_cruce_entre_apps(ruta, app)):
            problemas[str(ruta.relative_to(RAIZ))] = v
    assert not problemas, problemas


def test_config_no_importa_apps():
    for ruta in reglas.modulos_de_produccion():
        if ruta.relative_to(RAIZ).parts[0] == "config" and "settings" not in ruta.parts:
            texto = ruta.read_text(encoding="utf-8")
            assert "from apps" not in texto and "import apps" not in texto, ruta


def test_las_politicas_son_puras():
    politicas = _archivos("politicas.py")
    assert politicas, "debe existir cuentas/politicas.py"
    assert not [p for a in politicas if (p := reglas.violaciones_politicas_impuras(a))]


def test_solo_el_modulo_de_politicas_decide_permisos_y_existe_en_cuentas():
    assert (RAIZ / "apps/cuentas/politicas.py").exists()
    assert not any(a.parent.name != "cuentas" for a in _archivos("politicas.py")), "las políticas son únicas y viven en cuentas"


def test_el_dominio_no_conoce_clientes_de_red_solo_los_adaptadores():
    problemas = {}
    for ruta in reglas.modulos_de_produccion():
        partes = ruta.relative_to(RAIZ).parts
        if "adaptadores" in partes or "simulados" in partes or ruta.name == "afiliacion_servidor.py":
            continue
        if v := reglas.violaciones_dominio_con_cliente_de_red(ruta):
            problemas[str(ruta.relative_to(RAIZ))] = v
    assert not problemas, problemas


def test_solo_auditoria_conoce_su_tabla():
    problemas = {}
    for ruta in reglas.modulos_de_produccion():
        if _app_de(ruta) == "auditoria":
            continue
        if v := reglas.violaciones_tabla_de_auditoria(ruta):
            problemas[str(ruta.relative_to(RAIZ))] = v
    assert not problemas, problemas


def test_las_migraciones_ajenas_no_tocan_la_tabla_de_auditoria():
    for ruta in (RAIZ / "apps").rglob("migrations/*.py"):
        if _app_de(ruta) != "auditoria":
            assert not reglas.violaciones_tabla_de_auditoria(ruta), ruta


def test_las_plantillas_no_tienen_logica_ni_consultas_ni_scripts_en_linea():
    plantillas = list((RAIZ / "templates").rglob("*.html")) + list((RAIZ / "apps").rglob("templates/**/*.html"))
    assert len(plantillas) >= 15
    problemas = {str(p.relative_to(RAIZ)): v for p in plantillas if (v := reglas.violaciones_plantilla(p))}
    assert not problemas, problemas


def test_cada_app_tiene_la_estructura_de_capas_esperada():
    for app in APPS:
        base = RAIZ / "apps" / app
        assert (base / "modelos.py").exists() and (base / "tests").is_dir(), app
        assert (base / "servicios.py").exists(), f"{app} sin servicios.py"


# --- Pruebas de las pruebas: cada regla detecta una violación real ------------------------------------------------------------


def test_META_la_regla_de_vista_fina_detecta_orm_modelos_y_permisos(tmp_path):
    malo = tmp_path / "vistas.py"
    malo.write_text(
        "from django.db import transaction\nfrom apps.cuentas.modelos import Usuario\n"
        "def vista(request):\n    u = Usuario.objects.get(pk=1)\n    u.save()\n    if request.user.rol == 'AFILIADO':\n        pass\n",
        encoding="utf-8",
    )
    problemas = " | ".join(reglas.violaciones_vista_fina(malo))
    for esperado in ("django.db", "modelos", ".objects", ".save", ".rol"):
        assert esperado in problemas, (esperado, problemas)


def test_META_la_regla_de_escrituras_detecta_save_create_y_update(tmp_path):
    malo = tmp_path / "ayuda.py"
    malo.write_text("def f(m):\n    m.objects.create(a=1)\n    m.objects.filter(a=1).update(b=2)\n    x.save()\n", encoding="utf-8")
    assert len(reglas.violaciones_escritura_fuera_de_servicios(malo)) == 3
    ok = tmp_path / "servicios.py"
    ok.write_text(malo.read_text(), encoding="utf-8")
    assert reglas.violaciones_escritura_fuera_de_servicios(ok) == []  # en servicios.py sí está permitido
    super_ok = tmp_path / "modelos.py"
    super_ok.write_text("class M:\n    def save(self):\n        super().save()\n", encoding="utf-8")
    assert reglas.violaciones_escritura_fuera_de_servicios(super_ok) == []


def test_META_la_regla_de_cruces_acepta_servicios_y_rechaza_el_resto(tmp_path):
    f = tmp_path / "x.py"
    f.write_text("from apps.cuentas import servicios\nfrom apps.cuentas.politicas import nivel\nfrom apps.auditoria import servicios as a\n", encoding="utf-8")
    assert reglas.violaciones_cruce_entre_apps(f, "turnos") == []
    for malo in ("from apps.cuentas.modelos import Usuario", "from apps.cuentas import modelos", "import apps.turnos.vistas",
                 "from apps.auditoria.politicas import x", "from apps.integraciones.adaptadores.http import AfiliacionHttp"):
        f.write_text(malo + "\n", encoding="utf-8")
        assert reglas.violaciones_cruce_entre_apps(f, "delegaciones"), malo


def test_META_la_regla_de_auditoria_detecta_el_modelo_y_el_sql(tmp_path):
    for malo in ("EventoAuditoria.objects.create()", "cursor.execute('INSERT INTO auditoria_evento ...')"):
        f = tmp_path / "x.py"
        f.write_text(malo, encoding="utf-8")
        assert reglas.violaciones_tabla_de_auditoria(f)


def test_META_la_regla_de_politicas_detecta_orm_e_io(tmp_path):
    f = tmp_path / "politicas.py"
    f.write_text("from enum import Enum\nfrom django.db import models\nimport requests\n", encoding="utf-8")
    assert len(reglas.violaciones_politicas_impuras(f)) == 2


def test_META_la_regla_de_plantillas_detecta_logica_consultas_y_scripts(tmp_path):
    f = tmp_path / "x.html"
    for malo in ("{{ turnos.objects.all }}", "{{ nota|safe }}", "{% if user.rol == 'X' %}{% endif %}",
                 "<script>alert(1)</script>", '<a onclick="x()">', '<p style="color:red">', "{% autoescape off %}x{% endautoescape %}"):
        f.write_text(malo, encoding="utf-8")
        assert reglas.violaciones_plantilla(f), malo
    f.write_text('<script src="/static/a.js" defer></script><p>{{ form.cedula }}</p>', encoding="utf-8")
    assert reglas.violaciones_plantilla(f) == []


def test_META_la_regla_de_red_detecta_clientes_http_en_el_dominio(tmp_path):
    f = tmp_path / "x.py"
    for malo in ("import urllib.request", "import requests", "from apps.integraciones.adaptadores.http import AfiliacionHttp"):
        f.write_text(malo, encoding="utf-8")
        assert reglas.violaciones_dominio_con_cliente_de_red(f), malo

"""Reglas de arquitectura como funciones sobre código fuente. Las usan las pruebas reales y las pruebas de las pruebas
(que comprueban que cada regla SÍ detecta una violación).

Capas: vistas (finas) -> servicios (reglas, único camino de escritura al ORM) -> persistencia (modelos y restricciones).
"""
import ast
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
APPS = ("auditoria", "cuentas", "delegaciones", "integraciones", "notificaciones", "turnos")

# Métodos que escriben en la base. `update` se trata aparte (también existe en dict/set).
ESCRITURAS = {"save", "delete", "create", "bulk_create", "bulk_update", "get_or_create", "update_or_create"}
PERMISOS_SUELTOS = {"rol", "is_staff", "is_superuser", "has_perm", "has_perms", "groups", "user_permissions"}
RED_O_BD_PROHIBIDA_EN_DOMINIO = ("urllib.request", "http.client", "requests", "httpx", "socket")


def modulos_de_produccion(raiz: Path = RAIZ):
    """Archivos .py de apps/ y config/ que no son pruebas ni migraciones."""
    for base in ("apps", "config"):
        for ruta in (raiz / base).rglob("*.py"):
            partes = set(ruta.relative_to(raiz).parts)
            if {"tests", "migrations", "__pycache__"} & partes:
                continue
            yield ruta


def _arbol(ruta: Path) -> ast.AST:
    return ast.parse(ruta.read_text(encoding="utf-8"), filename=str(ruta))


def _importados(arbol: ast.AST):
    """(módulo, nombres) de cada importación absoluta; relativas se ignoran (son internas de la app)."""
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            for alias in nodo.names:
                yield alias.name, ()
        elif isinstance(nodo, ast.ImportFrom) and nodo.level == 0 and nodo.module:
            yield nodo.module, tuple(a.name for a in nodo.names)


# --- Reglas ------------------------------------------------------------------------------------------------------------------


def violaciones_vista_fina(ruta: Path) -> list[str]:
    """Vistas/esquemas/urls: sin ORM, sin modelos y sin comprobaciones de permisos propias."""
    problemas = []
    arbol = _arbol(ruta)
    for modulo, nombres in _importados(arbol):
        if modulo.startswith("django.db"):
            problemas.append(f"importa {modulo} (ORM)")
        if re.fullmatch(r"apps\.\w+\.(modelos|models)", modulo) or any(n in ("modelos", "models") for n in nombres if modulo.startswith("apps.")):
            problemas.append(f"importa modelos ({modulo})")
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Attribute):
            if nodo.attr == "objects":
                problemas.append(f"línea {nodo.lineno}: consulta directa al ORM (.objects)")
            if nodo.attr in ESCRITURAS:
                problemas.append(f"línea {nodo.lineno}: escritura en la vista (.{nodo.attr})")
            if nodo.attr in PERMISOS_SUELTOS:
                problemas.append(f"línea {nodo.lineno}: permiso comprobado en la vista (.{nodo.attr}); usar cuentas.politicas")
    return problemas


def violaciones_escritura_fuera_de_servicios(ruta: Path) -> list[str]:
    """Las escrituras al ORM viven solo en servicios.py (y en métodos propios de los modelos que delegan en super())."""
    if ruta.name == "servicios.py":
        return []
    problemas = []
    for nodo in ast.walk(_arbol(ruta)):
        if not (isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Attribute)):
            continue
        receptor = ast.unparse(nodo.func.value)
        if nodo.func.attr in ESCRITURAS and not receptor.startswith("super("):
            problemas.append(f"línea {nodo.lineno}: .{nodo.func.attr}() fuera de servicios.py")
        if nodo.func.attr == "update" and (".objects" in receptor or ".filter(" in receptor or ".exclude(" in receptor):
            problemas.append(f"línea {nodo.lineno}: queryset.update() fuera de servicios.py")
    return problemas


def violaciones_cruce_entre_apps(ruta: Path, app_propia: str) -> list[str]:
    """Una app solo importa de otra su `servicios.py` (y, para autorizar, `cuentas.politicas`)."""
    problemas = []
    for modulo, nombres in _importados(_arbol(ruta)):
        m = re.match(r"apps\.(\w+)(?:\.(\w+))?", modulo)
        if not m or m.group(1) == app_propia:
            continue
        otra, parte = m.group(1), m.group(2)
        if parte is None:  # `from apps.otra import x`: x debe ser servicios (o politicas en cuentas)
            permitidos = {"servicios"} | ({"politicas"} if otra == "cuentas" else set())
            ilegales = [n for n in nombres if n not in permitidos]
            if ilegales or not nombres:
                problemas.append(f"importa {ilegales or modulo} de apps.{otra}")
        elif parte != "servicios" and not (otra == "cuentas" and parte == "politicas"):
            problemas.append(f"importa apps.{otra}.{parte} (solo se permite servicios)")
    return problemas


def violaciones_politicas_impuras(ruta: Path) -> list[str]:
    permitidos = ("enum", "config.parametros")
    problemas = []
    for modulo, _ in _importados(_arbol(ruta)):
        if not modulo.startswith(permitidos):
            problemas.append(f"importa {modulo} (las políticas solo pueden usar enum y config.parametros)")
    return problemas


def violaciones_dominio_con_cliente_de_red(ruta: Path) -> list[str]:
    """Fuera de los adaptadores, nadie habla HTTP: el dominio depende de la interfaz (puerto)."""
    problemas = []
    for modulo, _ in _importados(_arbol(ruta)):
        if modulo.startswith(RED_O_BD_PROHIBIDA_EN_DOMINIO) or re.match(r"apps\.integraciones\.adaptadores", modulo):
            problemas.append(f"importa {modulo}")
    return problemas


def violaciones_tabla_de_auditoria(ruta: Path) -> list[str]:
    """Solo `auditoria` conoce su tabla: ni el modelo ni SQL crudo sobre ella fuera de esa app."""
    texto = ruta.read_text(encoding="utf-8")
    problemas = []
    if "EventoAuditoria" in texto:
        problemas.append("referencia EventoAuditoria")
    if "auditoria_evento" in texto:
        problemas.append("referencia la tabla auditoria_evento")
    return problemas


_PROHIBIDO_EN_PLANTILLAS = [
    (r"\.objects\b|\.filter\b|\.exclude\b|\.get_queryset\b|\.raw\b|\.count\b", "consulta o cálculo de datos"),
    (r"\|\s*safe\b|mark_safe|\{%\s*autoescape\s+off", "contenido marcado como seguro"),
    (r"\b(user|request\.user)\.(rol|is_staff|is_superuser|has_perm|groups)\b|\bperms\.|\.has_perm", "permiso decidido en la plantilla"),
    (r"\{%\s*(sql|raw_sql)\b", "SQL en plantilla"),
    (r"<script(?![^>]*\bsrc=)", "script en línea"),
    (r"\son\w+\s*=\s*[\"']", "manejador de evento en línea"),
    (r"\sstyle\s*=\s*[\"']", "estilo en línea"),
]


def violaciones_plantilla(ruta: Path) -> list[str]:
    texto = ruta.read_text(encoding="utf-8")
    return [f"{descripcion}: /{patron}/" for patron, descripcion in _PROHIBIDO_EN_PLANTILLAS if re.search(patron, texto)]

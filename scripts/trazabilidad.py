"""Genera docs/trazabilidad.md cruzando requisito, riesgo, control, prueba y módulo (Anexo C) a partir de los NOMBRES de las pruebas.

Convención: `test_T07_…` (prueba del §10.1) o `test_CA09_…` / `test_CA_09_…` (caso de abuso del Anexo G).
Sale con código 1 si algún T01-T18, CA-01..CA-16, riesgo, requisito o módulo queda sin prueba.
Uso: python scripts/trazabilidad.py [--solo-comprobar]
"""
import ast
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from trazabilidad_datos import CASOS, MODULOS, PRUEBAS, REQUISITOS, RIESGOS  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
PATRON = re.compile(r"^test_(T\d{2}b?|CA_?\d{2})(?:_|$)")


def descubrir() -> dict[str, list[tuple[str, str]]]:
    """id -> [(archivo, función)] para cada prueba nombrada con un identificador."""
    halladas: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for base in ("apps", "tests"):
        for archivo in sorted((RAIZ / base).rglob("test_*.py")):
            for nodo in ast.walk(ast.parse(archivo.read_text(encoding="utf-8"))):
                if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    m = PATRON.match(nodo.name)
                    if m:
                        halladas[m.group(1).replace("_", "")].append((archivo.relative_to(RAIZ).as_posix(), nodo.name))
    return halladas


def modulo_de(ruta: str) -> str:
    partes = ruta.split("/")
    return partes[1] if partes[0] == "apps" else f"tests/{partes[1]}"


def celda(funcs: list[tuple[str, str]], maximo: int) -> str:
    if not funcs:
        return "**SIN PRUEBA**"
    texto = "<br>".join(f"`{f}::{n}`" for f, n in funcs[:maximo])
    return f"{len(funcs)}: {texto}" + (f"<br>… (+{len(funcs) - maximo})" if len(funcs) > maximo else "")


def main() -> int:
    pruebas = descubrir()
    por_riesgo: dict[str, set[str]] = defaultdict(set)
    for ident, (_, riesgos) in {**PRUEBAS, **CASOS}.items():
        for r in riesgos.split():
            if ident in pruebas:
                por_riesgo[r].add(ident)
    sin_prueba = [i for i in list(PRUEBAS) + list(CASOS) if i not in pruebas]
    riesgos_sin = [r for r in RIESGOS if not por_riesgo[r]]
    requisitos_sin = [q for q, d in REQUISITOS.items() if not any(t in pruebas for t in d[3].split())]
    modulos_sin = [m for m in MODULOS if not any(m in d[2].split() and any(t in pruebas for t in d[1].split()) for d in RIESGOS.values())]

    ts_ok = len(PRUEBAS) - len([i for i in sin_prueba if i in PRUEBAS])
    ca_ok = len(CASOS) - len([i for i in sin_prueba if i in CASOS])
    lineas = [
        "# Matriz de trazabilidad (generada)",
        "",
        f"> Generado por `scripts/trazabilidad.py` el {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC a partir de los nombres de las pruebas. No editar a mano.",
        "> Los vínculos vienen del Anexo C, §10.1, §10.2 y Anexo G del informe (`scripts/trazabilidad_datos.py`).",
        "",
        "## Resumen",
        "",
        f"- Pruebas T del §10.1 con función de prueba: **{ts_ok}/{len(PRUEBAS)}**",
        f"- Casos de abuso CA-01..CA-16 con función de prueba: **{ca_ok}/{len(CASOS)}**",
        f"- Riesgos R01..R23 con prueba: **{len(RIESGOS) - len(riesgos_sin)}/{len(RIESGOS)}**",
        f"- Requisitos I01..I14 con prueba: **{len(REQUISITOS) - len(requisitos_sin)}/{len(REQUISITOS)}**",
        f"- Módulos M01..M14 con prueba: **{len(MODULOS) - len(modulos_sin)}/{len(MODULOS)}**",
        f"- Funciones de prueba nombradas con identificador: **{sum(len(v) for v in pruebas.values())}**",
        "",
        "## Del requisito a la prueba",
        "",
        "| Requisito | Decisión | Riesgos | Control principal | Pruebas | Módulo |",
        "|---|---|---|---|---|---|",
    ]
    for q, (decision, riesgos, control, ts, mods) in REQUISITOS.items():
        marca = " ".join(t if t in pruebas else f"**{t}(falta)**" for t in ts.split())
        lineas.append(f"| {q} | {decision} | {riesgos} | {control} | {marca} | {mods} |")
    lineas += ["", "## Del riesgo al control, la prueba y el módulo", "", "| Riesgo | Control preventivo | Pruebas T | Casos de abuso | Módulo | Código que lo prueba |", "|---|---|---|---|---|---|"]
    for r, (control, ts, mods) in RIESGOS.items():
        ca = [c for c, (_, rs) in CASOS.items() if r in rs.split()]
        donde = sorted({modulo_de(f) for i in set(ts.split()) | set(ca) for f, _ in pruebas.get(i, [])})
        marca_t = " ".join(t if t in pruebas else f"**{t}(falta)**" for t in ts.split())
        lineas.append(f"| {r} | {control} | {marca_t} | {' '.join(ca) or '—'} | {mods} | {', '.join(donde) or '**sin prueba**'} |")
    lineas += ["", "## Pruebas T01 a T18", "", "| ID | Qué verifica | Riesgos | Funciones de prueba |", "|---|---|---|---|"]
    for t, (que, rs) in PRUEBAS.items():
        lineas.append(f"| {t} | {que} | {rs} | {celda(pruebas.get(t, []), 4)} |")
    lineas += ["", "## Casos de abuso CA-01 a CA-16", "", "| ID | Caso | Riesgos | Funciones de prueba |", "|---|---|---|---|"]
    for c, (que, rs) in CASOS.items():
        lineas.append(f"| {c[:2]}-{c[2:]} | {que} | {rs} | {celda(pruebas.get(c, []), 3)} |")
    lineas += ["", "## Módulos M01-M14", "", "| Módulo | Nombre | Riesgos que cubre | Con prueba |", "|---|---|---|---|"]
    for m, nombre in MODULOS.items():
        rs = sorted(r for r, d in RIESGOS.items() if m in d[2].split())
        lineas.append(f"| {m} | {nombre} | {' '.join(rs)} | {'sí' if m not in modulos_sin else '**no**'} |")

    if "--solo-comprobar" not in sys.argv:
        (RAIZ / "docs" / "trazabilidad.md").write_text("\n".join(lineas) + "\n", encoding="utf-8")
    mal = False
    for titulo, lista in (("Pruebas sin función", sin_prueba), ("Riesgos sin prueba", riesgos_sin), ("Requisitos sin prueba", requisitos_sin), ("Módulos sin prueba", modulos_sin)):
        if lista:
            mal = True
            print(f"AVISO {titulo}: {', '.join(lista)}")
    print("Trazabilidad INCOMPLETA." if mal else "Trazabilidad completa.")
    return 1 if mal else 0


if __name__ == "__main__":
    sys.exit(main())

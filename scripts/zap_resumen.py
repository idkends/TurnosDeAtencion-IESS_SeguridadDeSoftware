"""Resume el informe JSON de ZAP por nivel de riesgo y decide: 0 altos y todo medio justificado (IGNORE en scripts/zap_reglas.conf)."""
import json
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def justificadas() -> set[str]:
    ids = set()
    for linea in (RAIZ / "scripts" / "zap_reglas.conf").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^(\d+)\s+IGNORE\b", linea)
        if m:
            ids.add(m.group(1))
    return ids


def main(entrada: str, salida: str) -> int:
    datos = json.loads(Path(entrada).read_text(encoding="utf-8"))
    niveles = {"3": "alto", "2": "medio", "1": "bajo", "0": "informativo"}
    resumen = {"alto": [], "medio": [], "bajo": [], "informativo": []}
    for sitio in datos.get("site", []):
        for alerta in sitio.get("alerts", []):
            resumen[niveles.get(str(alerta.get("riskcode")), "informativo")].append(
                {"id": alerta.get("pluginid"), "nombre": alerta.get("name"), "instancias": len(alerta.get("instances", [])), "justificada": alerta.get("pluginid") in justificadas()}
            )
    medios_sin = [a for a in resumen["medio"] if not a["justificada"]]
    informe = {
        "zap": datos.get("@version"), "generado": datos.get("@generated"),
        "conteo": {k: len(v) for k, v in resumen.items()}, "hallazgos": resumen,
        "medios_sin_justificar": [a["nombre"] for a in medios_sin],
        "cumple": not resumen["alto"] and not medios_sin,
    }
    Path(salida).write_text(json.dumps(informe, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"conteo": informe["conteo"], "medios_sin_justificar": informe["medios_sin_justificar"], "cumple": informe["cumple"]}, indent=2, ensure_ascii=False))
    return 0 if informe["cumple"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))

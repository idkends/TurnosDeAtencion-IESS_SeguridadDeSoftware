"""Convierte el resumen de k6 en la evidencia de T09: p95, p99, errores y comparación con las metas del informe."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

META_P95_MS, META_ERRORES = 2000.0, 0.01


def main(entrada: str, salida: str) -> int:
    m = json.loads(Path(entrada).read_text(encoding="utf-8"))["metrics"]
    informe = {"fecha_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "metas": {"p95_ms": META_P95_MS, "errores": META_ERRORES}, "escenarios": {}}
    ok = True
    for esc in ("sostenida", "rafaga"):
        dur, fallos = m.get(f"http_req_duration{{escenario:{esc}}}", {}), m.get(f"http_req_failed{{escenario:{esc}}}", {})
        peticiones = m.get(f"http_reqs{{escenario:{esc}}}", {}).get("count", 0)
        p95, p99 = dur.get("p(95)", float("inf")), dur.get("p(99)", float("inf"))
        errores = fallos.get("value", 1.0)
        cumple = p95 <= META_P95_MS and errores < META_ERRORES and peticiones > 0
        ok &= cumple
        informe["escenarios"][esc] = {"solicitudes": peticiones, "p95_ms": round(p95, 1), "p99_ms": round(p99, 1), "tasa_errores": round(errores, 5), "cumple_metas": cumple}
    limitadas = m.get("abusador_limitado", {}).get("count", 0)
    informe["origen_abusivo_limitado"] = {"respuestas_429_o_403": limitadas, "cumple": limitadas > 0}
    informe["los_limites_no_bloquean_a_todos"] = ok and limitadas > 0   # legítimos sin errores mientras el abusador es limitado
    informe["cumple_metas"] = bool(informe["los_limites_no_bloquean_a_todos"])
    Path(salida).write_text(json.dumps(informe, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(informe, indent=2, ensure_ascii=False))
    return 0 if informe["cumple_metas"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))

#!/usr/bin/env bash
# Verificación automática de todo lo prometido en el informe, con UN comando:  make verify   (o:  bash scripts/verify.sh [--completo])
#
# Pasos, en orden: (1) pytest con cobertura (incluye la prueba de arquitectura) · (2) lint-imports · (3) check --deploy · (4) Bandit ·
# (5) pip-audit · (6) SBOM CycloneDX · (7) gitleaks (historial) · (8) cabeceras, cookies y TLS · (9) cadena de auditoría ·
# (10) trazabilidad · (11) pruebas que leen la evidencia recién generada.
# Con --completo se añaden: carga k6 (T09), análisis dinámico ZAP (T14) y restauración de respaldo con RPO/RTO (T08).
#
# Cada paso deja su informe en docs/evidencia/ y el resumen (fecha, versión del código, resultado) en docs/evidencia/verificacion_<fecha>.{json,md}.
# Sale con código 1 si algún paso falla. Requisitos: Docker (con Compose) y Python 3 en el anfitrión.
set -uo pipefail
cd "$(dirname "$0")/.."
export MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8
COMPLETO=0; [ "${1:-}" = "--completo" ] && COMPLETO=1
MARCA="$(date -u +%Y%m%dT%H%M%SZ)"
EV="docs/evidencia"
mkdir -p "$EV"
RESULTADOS="$(mktemp)"
VERDE=$'\033[32m'; ROJO=$'\033[31m'; FIN=$'\033[0m'

paso() {   # paso "<nombre>" <comando...>  → ejecuta, mide y registra (nombre|estado|segundos)
  local nombre="$1"; shift
  echo; echo "▌ $nombre"
  local t0; t0=$(date +%s)
  "$@"; local rc=$?
  local seg=$(( $(date +%s) - t0 ))
  if [ $rc -eq 0 ]; then echo "  ${VERDE}OK${FIN} ($seg s)"; echo "$nombre|ok|$seg" >> "$RESULTADOS"
  else echo "  ${ROJO}FALLA (código $rc)${FIN} ($seg s)"; echo "$nombre|falla|$seg" >> "$RESULTADOS"; fi
  return 0
}

pruebas() { docker compose --profile pruebas run --rm -T pruebas "$@"; }
herramienta() { docker run --rm -v "$(pwd):/app" -w /app iess-turnos-pruebas "$@"; }   # con Internet (pip-audit consulta PyPI)

# --- Preparación (desde cero): secretos, certificado, contenedores y datos sintéticos ---------------------------------------------------------
echo "▌ Preparación"
[ -f .env ] || python scripts/generar_env.py
[ -f docker/certs/localhost.pem ] || sh docker/certs/generar.sh
docker compose up -d --build > /dev/null 2>&1 || { echo "No se pudo levantar el entorno con Docker Compose."; exit 1; }
docker compose --profile pruebas build pruebas > /dev/null 2>&1
until [ "$(docker compose ps app --format '{{.Health}}')" = "healthy" ]; do sleep 3; done
docker compose --profile utilidades run --rm -T sembrar > /dev/null 2>&1 || true
echo "  entorno listo"

COMMIT="$(git rev-parse --short HEAD 2> /dev/null || echo sin-git)"
SUCIO="$(git status --porcelain 2> /dev/null | grep -v '^?? docs/evidencia' | head -1)"

# --- 1-2. Pruebas, cobertura y arquitectura --------------------------------------------------------------------------------------------------------
cobertura() {
  pruebas pytest -q -p no:cacheprovider --cov=apps --cov=config --cov-report="term" --cov-report="json:/app/$EV/cobertura_$MARCA.json" \
    --cov-fail-under="${COBERTURA_MINIMA:-85}" 2>&1 | tail -25
  return "${PIPESTATUS[0]}"
}
paso "1. pytest completo con cobertura (T01-T18, CA-01..CA-16 y arquitectura)" cobertura
paso "2. Contratos de arquitectura (import-linter)" pruebas lint-imports

# --- 3. Configuración de despliegue -----------------------------------------------------------------------------------------------------------
paso "3. manage.py check --deploy (producción, sin advertencias)" docker compose exec -T app python manage.py check --deploy --fail-level WARNING

# --- 4. Análisis estático ------------------------------------------------------------------------------------------------------------------------
bandit() {
  herramienta bandit -q -r apps config -x '*/tests/*' -ll -f json -o "$EV/bandit_$MARCA.json" > /dev/null 2>&1; local rc=$?
  herramienta python -c "import json;d=json.load(open('$EV/bandit_$MARCA.json'));m=d['metrics']['_totals'];print('  hallazgos: altos',m['SEVERITY.HIGH'],'medios',m['SEVERITY.MEDIUM'],'bajos',m['SEVERITY.LOW'],'· justificados con # nosec:',m['nosec'])"
  return $rc
}
paso "4. Bandit (sin hallazgos medios ni altos)" bandit

# --- 5. Dependencias vulnerables ------------------------------------------------------------------------------------------------------------------
pipaudit() {
  local ignorar=(); while read -r id resto; do case "$id" in ''|\#*) ;; *) ignorar+=(--ignore-vuln "$id") ;; esac; done < scripts/pip_audit_ignorar.txt
  herramienta pip-audit -r requirements.txt --require-hashes --disable-pip "${ignorar[@]}" -f json -o "$EV/pip_audit_$MARCA.json" > /dev/null 2>&1; local rc=$?
  herramienta python -c "import json;d=json.load(open('$EV/pip_audit_$MARCA.json'));v=[x['name'] for x in d['dependencies'] if x.get('vulns')];print('  paquetes revisados:',len(d['dependencies']),'· con vulnerabilidades:',v or 'ninguno')"
  return $rc
}
paso "5. pip-audit (falla con vulnerabilidades sin justificar en scripts/pip_audit_ignorar.txt)" pipaudit

# --- 6. SBOM ---------------------------------------------------------------------------------------------------------------------------------------
sbom() { herramienta cyclonedx-py requirements requirements.txt --of JSON -o "$EV/sbom_$MARCA.json" > /dev/null 2>&1 && [ -s "$EV/sbom_$MARCA.json" ]; }
paso "6. SBOM CycloneDX" sbom

# --- 7. Secretos (historial completo) ---------------------------------------------------------------------------------------------------------------
gitleaks() {
  docker run --rm -v "$(pwd):/repo" zricethezav/gitleaks:latest detect --source /repo --config /repo/.gitleaks.toml --no-banner --redact \
    --report-format json --report-path "/repo/$EV/gitleaks_$MARCA.json" 2>&1 | tail -3
  return "${PIPESTATUS[0]}"
}
paso "7. gitleaks (secretos en todo el historial de git)" gitleaks

# --- 8-9. Cabeceras/cookies/TLS y cadena de auditoría --------------------------------------------------------------------------------------------
paso "8. Cabeceras, cookies y TLS contra la aplicación en ejecución" python scripts/verificar_cabeceras.py
paso "9. Verificación de la cadena de auditoría" docker compose exec -T app python manage.py verificar_auditoria

# --- 10. Trazabilidad -------------------------------------------------------------------------------------------------------------------------------
paso "10. Trazabilidad (ningún riesgo, requisito, prueba T ni caso CA sin prueba)" python scripts/trazabilidad.py

# --- Opcionales (--completo) ------------------------------------------------------------------------------------------------------------------------
if [ $COMPLETO -eq 1 ]; then
  paso "C1. Carga k6: 200 req/s, ráfaga de 400, 8 000 usuarios (T09)" bash scripts/carga.sh
  paso "C2. OWASP ZAP baseline, autenticado (T14)" bash scripts/zap.sh
  paso "C3. Respaldo y restauración con RPO/RTO medidos (T08)" bash -c 'bash scripts/respaldo.sh > /dev/null && bash scripts/restaurar.sh'
fi

# --- 11. Pruebas que leen la evidencia generada arriba ---------------------------------------------------------------------------------------------
paso "11. Pruebas de evidencia (T08, T09, T14, T15 leen los informes recién generados)" \
  pruebas pytest -q -p no:cacheprovider tests/seguridad/test_verificacion_informe.py tests/seguridad/test_respaldos.py

# --- Resumen ---------------------------------------------------------------------------------------------------------------------------------------
python - "$RESULTADOS" "$EV/verificacion_$MARCA" "$COMMIT" "${SUCIO:+si}" <<'PY'
import json, sys
from datetime import datetime, timezone
filas = [l.rstrip("\n").split("|") for l in open(sys.argv[1], encoding="utf-8") if l.strip()]
pasos = [{"paso": n, "resultado": e, "segundos": int(s)} for n, e, s in filas]
ok = all(p["resultado"] == "ok" for p in pasos)
datos = {"fecha_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "version_codigo": sys.argv[3], "cambios_sin_confirmar": bool(sys.argv[4]),
         "resultado": "VERDE" if ok else "ROJO", "pasos": pasos}
open(sys.argv[2] + ".json", "w", encoding="utf-8").write(json.dumps(datos, indent=2, ensure_ascii=False))
md = [f"# Verificación {datos['fecha_utc']}", "", f"- Versión del código: `{datos['version_codigo']}`" + (" (con cambios sin confirmar)" if datos["cambios_sin_confirmar"] else ""),
      f"- Resultado: **{datos['resultado']}**", "", "| Paso | Resultado | Segundos |", "|---|---|---|"]
md += [f"| {p['paso']} | {'✔' if p['resultado'] == 'ok' else '✘ FALLA'} | {p['segundos']} |" for p in pasos]
open(sys.argv[2] + ".md", "w", encoding="utf-8").write("\n".join(md) + "\n")
print("\n" + "═" * 78 + f"\n VERIFICACIÓN: {datos['resultado']}  (versión {datos['version_codigo']})")
for p in pasos:
    print(("  ✔ " if p["resultado"] == "ok" else "  ✘ ") + p["paso"])
sys.exit(0 if ok else 1)
PY

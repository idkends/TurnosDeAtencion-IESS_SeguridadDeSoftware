#!/usr/bin/env bash
# Prueba de carga con k6 (T09): ejecuta scripts/carga/disponibilidad.js dentro de la red de aplicación y compara con las metas
# del informe (p95 <= 2 s, errores < 1 %). Escribe docs/evidencia/carga_<fecha>.json. Sale con 1 si no se cumplen las metas.
set -euo pipefail
cd "$(dirname "$0")/.."
export MSYS_NO_PATHCONV=1
mkdir -p docs/evidencia
MARCA="$(date -u +%Y%m%dT%H%M%SZ)"
RESUMEN="docs/evidencia/k6_resumen_${MARCA}.json"

# Limpia los límites y bloqueos de ejecuciones anteriores para partir de cero.
docker compose exec -T redis sh -c 'redis-cli --no-auth-warning -a "$REDIS_CLAVE" flushdb' > /dev/null

docker run --rm --network iess-turnos_aplicacion -e BASE=http://app:8000 \
  -v "$(pwd)/scripts/carga:/carga:ro" -v "$(pwd)/docs/evidencia:/salida" \
  grafana/k6:latest run --quiet --summary-export "/salida/k6_resumen_${MARCA}.json" /carga/disponibilidad.js || CODIGO=$?

python scripts/carga_informe.py "$RESUMEN" "docs/evidencia/carga_${MARCA}.json"

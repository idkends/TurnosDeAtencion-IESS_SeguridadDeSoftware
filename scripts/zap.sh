#!/usr/bin/env bash
# Análisis dinámico (T11/T14, R16): baseline de OWASP ZAP contra la aplicación local, con y sin sesión.
# 1) Crea una cuenta sintética con clave aleatoria, inicia sesión con un cliente de prueba y entrega la cookie a ZAP.
# 2) ZAP rastrea el sitio (spider) y aplica las reglas pasivas. El informe queda en docs/evidencia/zap_<fecha>.{html,json}.
# 3) docs/evidencia/zap_resumen_<fecha>.json resume los hallazgos por riesgo; la prueba T14 exige 0 altos.
# Los hallazgos medios aceptados y su justificación están en scripts/zap_reglas.conf (IGNORE con comentario).
set -euo pipefail
cd "$(dirname "$0")/.."
export MSYS_NO_PATHCONV=1
MARCA="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p docs/evidencia

CREDENCIALES=$(docker compose exec -T app python manage.py usuarios_demo 5 | tr -d '
')
COOKIE=$(python scripts/demos/sesion.py "$CREDENCIALES" | tr -d '

')

cp scripts/zap_reglas.conf docs/evidencia/zap_reglas.conf   # ZAP busca el archivo de reglas dentro de su carpeta de trabajo
# ZAP corre en la red perimetral y entra por el proxy (como un usuario), reescribiendo Host al dominio permitido y agregando la cookie.
docker run --rm --network iess-turnos_perimetro -v "$(pwd)/docs/evidencia:/zap/wrk:rw" \
  ghcr.io/zaproxy/zaproxy:stable zap-baseline.py -t https://proxy:8443 -c zap_reglas.conf -m 3 -T 10 \
  -r "zap_${MARCA}.html" -J "zap_${MARCA}.json" \
  -z "-config replacer.full_list(0).description=host -config replacer.full_list(0).enabled=true -config replacer.full_list(0).matchtype=REQ_HEADER -config replacer.full_list(0).matchstr=Host -config replacer.full_list(0).regex=false -config replacer.full_list(0).replacement=localhost:8443 -config replacer.full_list(1).description=sesion -config replacer.full_list(1).enabled=true -config replacer.full_list(1).matchtype=REQ_HEADER -config replacer.full_list(1).matchstr=Cookie -config replacer.full_list(1).regex=false -config replacer.full_list(1).replacement=__Host-sessionid=${COOKIE}" \
  || true   # ZAP devuelve 1/2 con alertas; el veredicto lo da el resumen

python scripts/zap_resumen.py "docs/evidencia/zap_${MARCA}.json" "docs/evidencia/zap_resumen_${MARCA}.json"

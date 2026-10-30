#!/usr/bin/env bash
# Levanta el entorno INSEGURO de laboratorio (rama lab/inseguro) junto al de main, SIN mezclar datos ni claves:
#   - carpeta hermana ../iess-lab (git worktree de la rama lab/inseguro)
#   - proyecto Compose «iess-lab», puertos 9443 (HTTPS) y 19080, su propio .env con secretos propios y su propia base de datos.
# Nunca se integra a main. Para borrarlo todo: sh scripts/lab_bajar.sh
set -euo pipefail
cd "$(dirname "$0")/.."
export MSYS_NO_PATHCONV=1
DIR="${DIR_LAB:-../iess-lab}"

git rev-parse --verify -q lab/inseguro > /dev/null || { echo "No existe la rama lab/inseguro."; exit 1; }
[ -d "$DIR" ] || git worktree add "$DIR" lab/inseguro
cp docker/certs/*.pem "$DIR/docker/certs/" 2> /dev/null || (cd "$DIR" && sh docker/certs/generar.sh)
cd "$DIR"
if [ ! -f .env ]; then
  python scripts/generar_env.py
  sed -i 's#^URL_BASE=.*#URL_BASE=https://localhost:9443#; s#^ALLOWED_HOSTS=.*#ALLOWED_HOSTS=*#' .env   # ALLOWED_HOSTS=* es parte de la falla (f)
  printf 'PUERTO_HTTPS=9443\nPUERTO_HTTP=19080\n' >> .env
fi
docker compose -p iess-lab up -d --build
docker compose -p iess-lab --profile utilidades build sembrar
until [ "$(docker compose -p iess-lab ps app --format '{{.Health}}')" = "healthy" ]; do sleep 3; done
docker compose -p iess-lab --profile utilidades run --rm sembrar | tail -3
echo "Entorno inseguro listo en https://localhost:9443  (¡solo para demostraciones locales!)"

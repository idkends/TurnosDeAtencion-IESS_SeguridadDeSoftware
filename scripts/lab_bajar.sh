#!/usr/bin/env bash
# Apaga y borra el entorno inseguro de laboratorio (contenedores, volúmenes y la carpeta ../iess-lab).
set -euo pipefail
cd "$(dirname "$0")/.."
DIR="${DIR_LAB:-../iess-lab}"
[ -d "$DIR" ] && (cd "$DIR" && docker compose -p iess-lab down -v --remove-orphans)
git worktree remove --force "$DIR" 2> /dev/null || true
echo "Entorno inseguro eliminado."

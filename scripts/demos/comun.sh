#!/usr/bin/env bash
# Funciones comunes de las demostraciones «antes y después». Cada demo_X.sh llama a `correr_demo`.
#
#   LAB  = rama insegura (lab/inseguro) en el proyecto Compose «iess-lab»  → https://localhost:9443   (scripts/lab_levantar.sh)
#   MAIN = rama principal            en el proyecto Compose «iess-turnos» → https://localhost:8443
#
# Resultado esperado: el ataque FUNCIONA contra LAB y FALLA contra MAIN. Si no, la demo sale con error.
cd "$(dirname "$0")/../.."
export MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8
RAIZ="$(pwd)"
DIR_LAB="${DIR_LAB:-$RAIZ/../iess-lab}"
URL_LAB="${URL_LAB:-https://localhost:9443}"
URL_MAIN="${URL_MAIN:-https://localhost:8443}"

correr_demo() {   # $1 = letra, $2 = título, $3 = qué se rompe a propósito en la rama insegura
  local letra="$1" titulo="$2" intencional="$3"
  echo "════════════════════════════════════════════════════════════════════════════"
  echo " DEMO ${letra^^} — $titulo"
  echo " (Falla INTENCIONAL en lab/inseguro: $intencional)"
  echo "════════════════════════════════════════════════════════════════════════════"
  [ -d "$DIR_LAB" ] || { echo "Falta el entorno inseguro en $DIR_LAB. Ejecute: sh scripts/lab_levantar.sh"; exit 2; }
  echo; echo "▌ ANTES — rama INSEGURA (lab/inseguro) en $URL_LAB"
  python scripts/demos/ataques.py "$letra" --url "$URL_LAB" --directorio "$DIR_LAB" --proyecto iess-lab; local lab=$?
  echo; echo "▌ DESPUÉS — rama principal (main) en $URL_MAIN"
  python scripts/demos/ataques.py "$letra" --url "$URL_MAIN" --directorio "$RAIZ" --proyecto iess-turnos; local principal=$?
  echo; echo "────────────────────────────────────────────────────────────────────────────"
  if [ "$lab" -eq 0 ] && [ "$principal" -eq 1 ]; then
    echo " RESULTADO: ✔ el ataque funciona en lab/inseguro y FALLA en main (como se esperaba)."; exit 0
  fi
  echo " RESULTADO: ✘ inesperado (lab=$lab, main=$principal; se esperaba lab=0 y main=1)."; exit 1
}

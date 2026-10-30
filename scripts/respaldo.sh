#!/usr/bin/env bash
# Respaldo diario cifrado (Anexo I). Copia base de PostgreSQL (pg_basebackup) cifrada con AES-256 y guardada en un volumen
# DISTINTO del de la base (`respaldos`), más una copia en ./respaldos_locales (fuera de Docker). Junto con el archivo continuo
# de WAL (volumen `wal_archivo`) permite recuperar a un punto en el tiempo. Deja un asiento RESPALDO_REALIZADO/FALLIDO en la
# cadena de auditoría (reglas D15). Uso: scripts/respaldo.sh
set -euo pipefail
cd "$(dirname "$0")/.."
export MSYS_NO_PATHCONV=1   # Git Bash en Windows no debe reescribir rutas como /respaldos
set -a; . ./.env; set +a

NOMBRE="base_$(date -u +%Y%m%dT%H%M%SZ)"
RETENCION="${RESPALDO_RETENCION:-14}"

registrar() {
  docker compose --profile utilidades run --rm -T sembrar python manage.py registrar_evento_operativo "$@" > /dev/null || true
}
fallo() {
  echo "ERROR: el respaldo $NOMBRE falló." >&2
  registrar RESPALDO_FALLIDO --resultado ERROR --detalle "{\"nombre\":\"$NOMBRE\"}"
  exit 1
}
trap fallo ERR

docker compose exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" -e RESPALDO_CLAVE -e NOMBRE="$NOMBRE" -e DB_NAME -e RETENCION="$RETENCION" db bash -c '
set -euo pipefail
PSQL="psql -h /var/run/postgresql -U postgres -d $DB_NAME -Atq"
pg_basebackup -h /var/run/postgresql -U postgres -Ft -X fetch -D - \
  | openssl enc -aes-256-cbc -pbkdf2 -salt -pass env:RESPALDO_CLAVE -out "/respaldos/$NOMBRE.tar.enc"
$PSQL -c "select pg_switch_wal()" > /dev/null          # cierra el segmento: el WAL posterior al respaldo queda archivado
ultima=$($PSQL -c "select coalesce(max(secuencia),0) from auditoria_evento")
eventos=$($PSQL -c "select count(*) from auditoria_evento")
suma=$(sha256sum "/respaldos/$NOMBRE.tar.enc" | cut -d" " -f1)
printf "{\"nombre\":\"%s\",\"creado_utc\":\"%s\",\"ultima_secuencia\":%s,\"eventos\":%s,\"sha256\":\"%s\"}\n" \
  "$NOMBRE" "$(date -u +%FT%TZ)" "$ultima" "$eventos" "$suma" > "/respaldos/$NOMBRE.json"
# Retención: se conservan las últimas N copias.
ls -1t /respaldos/base_*.tar.enc | tail -n +$((RETENCION + 1)) | while read -r viejo; do rm -f "$viejo" "${viejo%.tar.enc}.json"; done
'

# Copia fuera del volumen de la base (y fuera de Docker): cifrada igual que la original.
mkdir -p respaldos_locales
docker compose cp "db:/respaldos/$NOMBRE.tar.enc" "respaldos_locales/$NOMBRE.tar.enc"
docker compose cp "db:/respaldos/$NOMBRE.json" "respaldos_locales/$NOMBRE.json"

trap - ERR
registrar RESPALDO_REALIZADO --detalle "$(cat "respaldos_locales/$NOMBRE.json")"
echo "Respaldo $NOMBRE listo (volumen 'respaldos' y ./respaldos_locales)."

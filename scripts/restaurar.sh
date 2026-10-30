#!/usr/bin/env bash
# Prueba de restauración en un ENTORNO LIMPIO (Anexo I): levanta un PostgreSQL nuevo, vacío y aislado, restaura la última copia
# base, reproduce el WAL archivado (PITR), verifica la cadena de auditoría con la aplicación y mide RPO y RTO.
# Escribe la evidencia en docs/evidencia/restauracion_<fecha>.json y registra RESTAURACION_PROBADA en la auditoría.
#
# Uso: scripts/restaurar.sh [--hasta "2026-10-30 15:00:00+00"]    (sin --hasta recupera hasta el último WAL disponible)
# Objetivos: RPO <= RPO_OBJETIVO_SEG (300 s) y RTO <= RTO_OBJETIVO_SEG (1800 s). Sale con código 1 si no se cumplen.
set -euo pipefail
cd "$(dirname "$0")/.."
export MSYS_NO_PATHCONV=1
set -a; . ./.env; set +a

HASTA=""
[ "${1:-}" = "--hasta" ] && HASTA="${2:?falta la fecha}"
RPO_OBJETIVO="${RPO_OBJETIVO_SEG:-300}"
RTO_OBJETIVO="${RTO_OBJETIVO_SEG:-1800}"
CONTENEDOR="iess_restauracion"
VOL_DATOS="iess_restauracion_datos"
RED="iess-turnos_datos"
PD="/var/lib/postgresql/18/docker"
INICIO=$(date +%s)

limpiar() { docker rm -f "$CONTENEDOR" > /dev/null 2>&1 || true; docker volume rm "$VOL_DATOS" > /dev/null 2>&1 || true; }
trap limpiar EXIT
limpiar

# Estado de la base de origen (si sigue viva) para medir cuánto se perdería: es la referencia del RPO.
origen() { docker compose exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" db psql -h /var/run/postgresql -U postgres -d "$DB_NAME" -Atq -c "$1"; }
ORIGEN_ULTIMO=$(origen "select coalesce(max(secuencia),0) from auditoria_evento" 2>/dev/null || echo "")
ORIGEN_FECHA=$(origen "select coalesce(extract(epoch from max(ocurrido_en)),0) from auditoria_evento" 2>/dev/null || echo "")

COPIA=$(docker compose exec -T db sh -c 'ls -1t /respaldos/base_*.tar.enc | head -n1' | tr -d '\r')
[ -n "$COPIA" ] || { echo "No hay copias en el volumen 'respaldos'." >&2; exit 1; }
MANIFIESTO="${COPIA%.tar.enc}.json"
echo "Restaurando $COPIA en un entorno limpio..."

docker run -d --name "$CONTENEDOR" --network "$RED" --network-alias db_restaurada \
  -v iess-turnos_respaldos:/respaldos:ro -v iess-turnos_wal_archivo:/wal_archivo:ro -v "$VOL_DATOS":/var/lib/postgresql \
  -e RESPALDO_CLAVE --entrypoint sleep postgres:18 infinity > /dev/null

docker exec -u root "$CONTENEDOR" sh -c "mkdir -p $PD && chown postgres:postgres $PD && chmod 700 $PD"
docker exec -u postgres -e RESPALDO_CLAVE "$CONTENEDOR" bash -c "
  set -euo pipefail
  openssl enc -d -aes-256-cbc -pbkdf2 -pass env:RESPALDO_CLAVE -in '$COPIA' | tar -x -C $PD
  touch $PD/recovery.signal
  {
    echo \"restore_command = 'cp /wal_archivo/%f %p'\"
    echo \"recovery_target_action = 'promote'\"
    [ -n '$HASTA' ] && echo \"recovery_target_time = '$HASTA'\" && echo \"recovery_target_inclusive = on\"
    true
  } >> $PD/postgresql.auto.conf
  pg_ctl -D $PD -o '-c listen_addresses=* -c archive_mode=off' -w -t 900 start > /dev/null
"

# Esperar a que termine la recuperación (salga del modo recuperación y acepte consultas).
for _ in $(seq 1 300); do
  [ "$(docker exec -u postgres -e PGPASSWORD="$POSTGRES_PASSWORD" "$CONTENEDOR" psql -h localhost -U postgres -d "$DB_NAME" -Atq -c 'select pg_is_in_recovery()' 2>/dev/null || echo t)" = "f" ] && break
  sleep 1
done
resto() { docker exec -u postgres -e PGPASSWORD="$POSTGRES_PASSWORD" "$CONTENEDOR" psql -h localhost -U postgres -d "$DB_NAME" -Atq -c "$1"; }
REST_ULTIMO=$(resto "select coalesce(max(secuencia),0) from auditoria_evento")
REST_FECHA=$(resto "select coalesce(extract(epoch from max(ocurrido_en)),0) from auditoria_evento")
USUARIOS=$(resto "select count(*) from cuentas_usuario")
TURNOS=$(resto "select count(*) from turnos_turno")

# La cadena se verifica con la aplicación (HMAC con la clave de auditoría), apuntando a la base restaurada.
CADENA="íntegra"
docker compose --profile utilidades run --rm -T -e DB_HOST=db_restaurada sembrar python manage.py verificar_auditoria > /tmp/verif.$$ 2>&1 || CADENA="ALTERADA"
RTO=$(( $(date +%s) - INICIO ))

# RPO: lo que habría faltado respecto del origen (si sigue vivo); si no hay origen, tiempo desde el último evento recuperado.
if [ -n "$ORIGEN_FECHA" ] && [ -z "$HASTA" ]; then
  RPO=$(python3 -c "print(max(0, round(float('$ORIGEN_FECHA') - float('$REST_FECHA'))))" 2>/dev/null || echo 0)
  PERDIDOS=$(( ORIGEN_ULTIMO - REST_ULTIMO ))
else
  RPO=$(python3 -c "import time; print(max(0, round(time.time() - float('$REST_FECHA'))))" 2>/dev/null || echo 0)
  PERDIDOS=-1
fi

mkdir -p docs/evidencia
SALIDA="docs/evidencia/restauracion_$(date -u +%Y%m%dT%H%M%SZ).json"
OK=true
{ [ "$CADENA" = "íntegra" ] && [ "$RPO" -le "$RPO_OBJETIVO" ] && [ "$RTO" -le "$RTO_OBJETIVO" ]; } || OK=false
cat > "$SALIDA" <<JSON
{
  "fecha_utc": "$(date -u +%FT%TZ)",
  "copia": "$(basename "$COPIA")",
  "recuperado_hasta": "${HASTA:-ultimo WAL disponible}",
  "rpo_segundos": $RPO,
  "rpo_objetivo_segundos": $RPO_OBJETIVO,
  "rto_segundos": $RTO,
  "rto_objetivo_segundos": $RTO_OBJETIVO,
  "eventos_auditoria_perdidos_vs_origen": $PERDIDOS,
  "ultima_secuencia_restaurada": $REST_ULTIMO,
  "usuarios_restaurados": $USUARIOS,
  "turnos_restaurados": $TURNOS,
  "cadena_auditoria": "$CADENA",
  "cumple_objetivos": $OK
}
JSON
cat "$SALIDA"

docker compose --profile utilidades run --rm -T sembrar python manage.py registrar_evento_operativo RESTAURACION_PROBADA \
  --resultado "$([ "$OK" = true ] && echo OK || echo ERROR)" --detalle "{\"rpo_s\":$RPO,\"rto_s\":$RTO,\"cadena\":\"$CADENA\"}" > /dev/null || true
[ "$OK" = true ] || { echo "La restauración NO cumple los objetivos." >&2; exit 1; }
echo "Restauración probada: RPO ${RPO}s (objetivo ${RPO_OBJETIVO}s), RTO ${RTO}s (objetivo ${RTO_OBJETIVO}s)."

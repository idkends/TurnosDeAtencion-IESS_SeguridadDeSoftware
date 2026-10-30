#!/bin/sh
# Roles de mínimo privilegio (informe §4.1, Anexo J). Se ejecuta UNA vez, al crear el volumen de datos.
# Las contraseñas llegan por variables de entorno del contenedor; nada secreto vive en este archivo.
set -eu

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v dbname="$POSTGRES_DB" \
  -v mig_clave="$DB_MIGRACIONES_CLAVE" \
  -v app_clave="$DB_CLAVE" \
  -v aud_clave="$DB_AUDITORIA_RO_CLAVE" \
  -v test_clave="$DB_CLAVE_PRUEBAS" <<'SQL'
-- Dueño del esquema: aplica migraciones (DDL). Se usa solo en el despliegue.
CREATE ROLE iess_migraciones LOGIN PASSWORD :'mig_clave' NOSUPERUSER NOCREATEDB NOCREATEROLE;
-- Usuario de la aplicación: solo DML. No puede crear, alterar ni borrar tablas.
CREATE ROLE iess_app LOGIN PASSWORD :'app_clave' NOSUPERUSER NOCREATEDB NOCREATEROLE;
-- Solo lectura de la tabla de auditoría (revisión, verificación de la cadena).
CREATE ROLE iess_auditoria_ro LOGIN PASSWORD :'aud_clave' NOSUPERUSER NOCREATEDB NOCREATEROLE;
-- SOLO DESARROLLO: crea y destruye la base temporal de las pruebas automáticas.
CREATE ROLE iess_pruebas LOGIN PASSWORD :'test_clave' NOSUPERUSER CREATEDB NOCREATEROLE;

ALTER DATABASE :"dbname" OWNER TO iess_migraciones;
REVOKE ALL ON DATABASE :"dbname" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"dbname" TO iess_app, iess_auditoria_ro, iess_migraciones;

ALTER ROLE iess_app SET statement_timeout = '10s';            -- tiempo máximo de solicitud (Anexo E)
ALTER ROLE iess_app SET idle_in_transaction_session_timeout = '15s';
ALTER ROLE iess_app SET lock_timeout = '5s';

ALTER SCHEMA public OWNER TO iess_migraciones;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO iess_app, iess_auditoria_ro;

-- Todo objeto que cree iess_migraciones concede DML a la aplicación; la auditoría se restringe en su migración.
ALTER DEFAULT PRIVILEGES FOR ROLE iess_migraciones IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO iess_app;
ALTER DEFAULT PRIVILEGES FOR ROLE iess_migraciones IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO iess_app;
SQL

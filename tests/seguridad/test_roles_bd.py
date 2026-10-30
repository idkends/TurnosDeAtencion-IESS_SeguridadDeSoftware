"""Roles de base de datos de mínimo privilegio (informe §4.1, Anexo J, R16, R22).

Se conectan con los roles REALES de Docker Compose contra la base `iess` ya migrada
(`docker compose run --rm migrar`), no contra la base temporal de pruebas.
"""
import os

import psycopg
import pytest

pytestmark = pytest.mark.roles_bd


def _conectar(usuario_env_clave, usuario):
    clave = os.environ.get(usuario_env_clave)
    if not clave:
        pytest.skip(f"Falta {usuario_env_clave}: ejecute con `docker compose --profile pruebas run pruebas`.")
    try:
        return psycopg.connect(
            host=os.environ.get("DB_HOST", "db"), dbname=os.environ.get("DB_NAME", "iess"), user=usuario, password=clave,
            connect_timeout=5, autocommit=True,
        )
    except psycopg.OperationalError as error:  # pragma: no cover
        pytest.fail(f"No se pudo conectar como {usuario}: {error}")


@pytest.fixture
def app():
    with _conectar("DB_CLAVE", "iess_app") as conexion:
        existe = conexion.execute("SELECT to_regclass('public.auditoria_evento')").fetchone()[0]
        assert existe, "La base `iess` no está migrada: ejecute `docker compose run --rm migrar` antes."
        yield conexion


@pytest.fixture
def auditoria_ro():
    with _conectar("DB_AUDITORIA_RO_CLAVE", "iess_auditoria_ro") as conexion:
        yield conexion


@pytest.fixture
def migraciones():
    with _conectar("DB_MIGRACIONES_CLAVE", "iess_migraciones") as conexion:
        yield conexion


def _rechazado(conexion, sql, *params):
    """El motor debe rechazar la sentencia con falta de privilegios."""
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conexion.execute(sql, params)


# --- Usuario de la aplicación: DML sí, DDL no --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE intruso (id int)",
        "DROP TABLE cuentas_usuario",
        "ALTER TABLE cuentas_usuario ADD COLUMN extra text",
        "DROP TABLE turnos_turno CASCADE",
        "TRUNCATE cuentas_usuario CASCADE",
        "CREATE EXTENSION pgcrypto",
        "CREATE ROLE otro LOGIN",
        "CREATE SCHEMA intruso",
        "DROP TRIGGER auditoria_sin_update_delete ON auditoria_evento",
        "ALTER TABLE auditoria_evento DISABLE TRIGGER ALL",
    ],
)
def test_la_aplicacion_no_puede_hacer_ddl_ni_administrar(app, sql):
    _rechazado(app, sql)


def test_la_aplicacion_no_puede_conceder_privilegios_a_nadie(app):
    # PostgreSQL no lanza error si quien no es dueño ni tiene "grant option" intenta conceder: la sentencia no
    # concede nada (solo avisa). Lo que importa es que el privilegio NO aparezca.
    app.execute("GRANT SELECT ON cuentas_usuario TO iess_auditoria_ro")
    app.execute("GRANT ALL ON cuentas_usuario TO PUBLIC")
    assert app.execute("SELECT has_table_privilege('iess_auditoria_ro', 'cuentas_usuario', 'SELECT')").fetchone()[0] is False
    assert app.execute("SELECT has_table_privilege('public', 'cuentas_usuario', 'SELECT')").fetchone()[0] is False


def test_la_aplicacion_si_puede_leer_y_escribir_datos_normales(app):
    n = app.execute("SELECT count(*) FROM turnos_establecimiento").fetchone()[0]
    app.execute("INSERT INTO turnos_establecimiento (id, codigo, nombre, activo) VALUES (gen_random_uuid(), 'ZZ-TEST', 'Prueba de privilegios', true)")
    app.execute("UPDATE turnos_establecimiento SET nombre = 'Cambiado' WHERE codigo = 'ZZ-TEST'")
    app.execute("DELETE FROM turnos_establecimiento WHERE codigo = 'ZZ-TEST'")
    assert app.execute("SELECT count(*) FROM turnos_establecimiento").fetchone()[0] == n


def test_la_aplicacion_solo_puede_leer_e_insertar_en_la_auditoria(app):
    app.execute("SELECT count(*) FROM auditoria_evento")  # leer: sí
    for sql in (
        "UPDATE auditoria_evento SET accion = 'X'",
        "DELETE FROM auditoria_evento",
        "TRUNCATE auditoria_evento",
    ):
        _rechazado(app, sql)


def test_la_aplicacion_no_puede_crear_objetos_en_el_esquema_publico(app):
    privilegio = app.execute("SELECT has_schema_privilege('iess_app', 'public', 'CREATE')").fetchone()[0]
    assert privilegio is False


def test_la_aplicacion_tiene_limites_de_tiempo_por_sentencia(app):
    assert app.execute("SHOW statement_timeout").fetchone()[0] == "10s"  # tiempo de solicitud del Anexo E


def test_la_aplicacion_no_es_superusuario_ni_crea_roles_ni_bases(app):
    fila = app.execute("SELECT rolsuper, rolcreaterole, rolcreatedb FROM pg_roles WHERE rolname = 'iess_app'").fetchone()
    assert fila == (False, False, False)


# --- Usuario de solo lectura para auditoría ---------------------------------------------------------------------------------------------------


def test_el_rol_de_auditoria_solo_lee_la_tabla_de_auditoria(auditoria_ro):
    auditoria_ro.execute("SELECT count(*) FROM auditoria_evento")
    for sql in (
        "SELECT * FROM cuentas_usuario",  # ni datos personales
        "SELECT * FROM turnos_turno",
        "INSERT INTO auditoria_evento (uuid, ocurrido_en, accion, resultado, detalle, hash_anterior, hash, version_clave) "
        "VALUES (gen_random_uuid(), now(), 'X', 'OK', '{}', 'a', 'b', 1)",
        "UPDATE auditoria_evento SET accion = 'X'",
        "DELETE FROM auditoria_evento",
        "CREATE TABLE t (id int)",
    ):
        _rechazado(auditoria_ro, sql)


# --- Usuario de migraciones -----------------------------------------------------------------------------------------------------------------------


def test_el_rol_de_migraciones_si_puede_hacer_ddl(migraciones):
    migraciones.execute("BEGIN")
    migraciones.execute("CREATE TABLE prueba_ddl (id int)")
    migraciones.execute("ALTER TABLE prueba_ddl ADD COLUMN x text")
    migraciones.execute("DROP TABLE prueba_ddl")
    migraciones.execute("ROLLBACK")


def test_la_unica_extension_instalada_ademas_de_plpgsql_es_btree_gist(app):
    extensiones = {fila[0] for fila in app.execute("SELECT extname FROM pg_extension").fetchall()}
    assert extensiones == {"plpgsql", "btree_gist"}, extensiones


def test_las_restricciones_criticas_existen_en_la_base_real(app):
    nombres = {fila[0] for fila in app.execute("SELECT conname FROM pg_constraint").fetchall()}
    indices = {fila[0] for fila in app.execute("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'").fetchall()}
    assert "ex_turno_sin_solape" in nombres  # RT-03
    assert {"uq_turno_afiliado_especialidad", "uq_turno_franja", "uq_idempotencia"} <= indices | nombres  # RT-01, RT-04, idempotencia
    assert {"ck_turno_estado", "ck_franja_estado"} <= nombres  # CHECK de estados

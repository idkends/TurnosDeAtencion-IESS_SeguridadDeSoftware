"""La base rechaza UPDATE, DELETE y TRUNCATE sobre la auditoría (R22, CA-10): permisos y disparador."""
from django.db import migrations

SQL_ADELANTE = """
CREATE FUNCTION auditoria_solo_adicion() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'La tabla de auditoría es de solo adición (%)', TG_OP USING ERRCODE = 'insufficient_privilege';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER auditoria_sin_update_delete
    BEFORE UPDATE OR DELETE ON auditoria_evento
    FOR EACH ROW EXECUTE FUNCTION auditoria_solo_adicion();

CREATE TRIGGER auditoria_sin_truncate
    BEFORE TRUNCATE ON auditoria_evento
    FOR EACH STATEMENT EXECUTE FUNCTION auditoria_solo_adicion();

-- Permisos de mínimo privilegio (si los roles existen: en el despliegue de Compose siempre existen).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'iess_app') THEN
        REVOKE ALL ON auditoria_evento FROM iess_app;
        GRANT SELECT, INSERT ON auditoria_evento TO iess_app;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'iess_auditoria_ro') THEN
        REVOKE ALL ON auditoria_evento FROM iess_auditoria_ro;
        GRANT SELECT ON auditoria_evento TO iess_auditoria_ro;
    END IF;
END
$$;
"""

SQL_ATRAS = """
DROP TRIGGER IF EXISTS auditoria_sin_truncate ON auditoria_evento;
DROP TRIGGER IF EXISTS auditoria_sin_update_delete ON auditoria_evento;
DROP FUNCTION IF EXISTS auditoria_solo_adicion();
"""


class Migration(migrations.Migration):
    dependencies = [("auditoria", "0001_initial")]
    operations = [migrations.RunSQL(SQL_ADELANTE, SQL_ATRAS)]

"""⚠ INSEGURO A PROPÓSITO (rama lab/inseguro, demo D). NUNCA se integra a main.

Quita el disparador de solo adición y devuelve UPDATE/DELETE a la aplicación: la auditoría pasa a ser editable.
"""
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("auditoria", "0003_etapa3")]
    operations = [
        migrations.RunSQL(
            "DROP TRIGGER IF EXISTS auditoria_sin_update_delete ON auditoria_evento;"
            "DROP TRIGGER IF EXISTS auditoria_sin_truncate ON auditoria_evento;"
            "GRANT UPDATE, DELETE ON auditoria_evento TO iess_app;",
            reverse_sql=migrations.RunSQL.noop,
        )
    ]

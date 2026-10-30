"""⚠ INSEGURO A PROPÓSITO (rama lab/inseguro, demo C). NUNCA se integra a main.

Quita el índice único parcial que impide dos turnos activos en la misma franja (RT-04). Sin él, la base ya no protege contra la doble reserva.
"""
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("turnos", "0003_etapa3")]
    operations = [migrations.RunSQL("DROP INDEX IF EXISTS uq_turno_franja;", reverse_sql=migrations.RunSQL.noop)]

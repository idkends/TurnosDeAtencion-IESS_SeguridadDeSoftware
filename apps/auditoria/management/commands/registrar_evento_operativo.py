"""Registra en la cadena un evento operativo de los scripts (respaldos, pruebas de restauración, escaneo de dependencias).

Solo acepta una lista blanca de acciones y un detalle JSON pequeño (la redacción de secretos se aplica igual).
Lo usan scripts/respaldo.sh, scripts/restaurar.sh y, en la etapa 4, la integración continua (D15, D16).
"""
import json

from django.core.management.base import BaseCommand, CommandError

from apps.auditoria import servicios

PERMITIDAS = {"RESPALDO_REALIZADO", "RESPALDO_FALLIDO", "RESTAURACION_PROBADA", "VULNERABILIDAD_DETECTADA"}


class Command(BaseCommand):
    help = "Registra un evento operativo en la cadena de auditoría."

    def add_arguments(self, parser):
        parser.add_argument("accion", choices=sorted(PERMITIDAS))
        parser.add_argument("--detalle", default="{}", help="JSON con el detalle (sin secretos).")
        parser.add_argument("--resultado", default="OK", choices=["OK", "ERROR"])

    def handle(self, *args, **opciones):
        try:
            detalle = json.loads(opciones["detalle"])
        except json.JSONDecodeError as error:
            raise CommandError("El detalle no es JSON válido.") from error
        if not isinstance(detalle, dict) or len(opciones["detalle"]) > 2000:
            raise CommandError("El detalle debe ser un objeto JSON pequeño.")
        e = servicios.registrar(accion=opciones["accion"], resultado=opciones["resultado"], recurso_tipo="operacion", detalle=detalle)
        self.stdout.write(f"Evento {e.secuencia} registrado")

"""Tarea programada de vencimiento de datos (sección 2.2 del informe): elimina, anonimiza o archiva y deja constancia."""
from django.conf import settings
from django.core.management.base import BaseCommand

from apps.derechos import servicios


class Command(BaseCommand):
    help = "Aplica los plazos de conservación de datos y muestra la constancia."

    def handle(self, *args, **opciones):
        destino = str(settings.BASE_DIR / "var" / "archivo_auditoria.jsonl")
        for c in servicios.ejecutar_vencimiento(archivo_auditoria=destino):
            self.stdout.write(f"{c.categoria}: {c.accion} {c.cantidad}")
        self.stdout.write(str(servicios.procesar_ordenes_encargados()))

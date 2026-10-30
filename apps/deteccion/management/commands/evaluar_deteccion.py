"""Evalúa las reglas D01-D18 sobre los eventos de auditoría (la tarea programada lo ejecuta cada minuto)."""
from django.core.management.base import BaseCommand

from apps.deteccion import servicios


class Command(BaseCommand):
    help = "Evalúa las reglas de detección y muestra las alertas nuevas."

    def handle(self, *args, **opciones):
        nuevas = servicios.evaluar()
        for a in nuevas:
            self.stdout.write(f"{a.regla} {a.severidad} {a.titulo}")
        self.stdout.write(f"{len(nuevas)} alerta(s) nueva(s)")

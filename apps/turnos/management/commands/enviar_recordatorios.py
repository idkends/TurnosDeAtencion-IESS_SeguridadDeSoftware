"""Recordatorios neutrales a las 72 h y 24 h antes de la cita."""
from django.core.management.base import BaseCommand

from apps.turnos import servicios


class Command(BaseCommand):
    help = "Envía los recordatorios de citas pendientes de aviso."

    def handle(self, *args, **opciones):
        self.stdout.write(str(servicios.enviar_recordatorios()))

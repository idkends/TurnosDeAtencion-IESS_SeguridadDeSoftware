"""Libera los cupos de turnos RESERVADOS sin confirmar en 24 h (RT-05) y cancela los PENDIENTES sin confirmación de la agenda."""
from django.core.management.base import BaseCommand

from apps.turnos import servicios


class Command(BaseCommand):
    help = "Expira turnos vencidos y libera su cupo."

    def handle(self, *args, **opciones):
        self.stdout.write(str(servicios.expirar_vencidos()))

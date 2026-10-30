"""Conciliación con el sistema de agenda: reintenta confirmar los turnos PENDIENTE (RT-10)."""
from django.core.management.base import BaseCommand

from apps.turnos import servicios


class Command(BaseCommand):
    help = "Concilia los turnos pendientes con la agenda."

    def handle(self, *args, **opciones):
        self.stdout.write(str(servicios.conciliar_pendientes()))

"""Tareas programadas: bucle simple (cada N segundos) que expira turnos y concilia con la agenda.

Es un proceso aparte (servicio `tareas` de Compose). Un fallo de una iteración se registra y no detiene el bucle.
Sin datos personales en los registros: solo contadores.
"""
import logging
import pathlib
import time

from django.core.management.base import BaseCommand

from apps.turnos import servicios

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Ejecuta periódicamente la expiración de turnos y la conciliación con la agenda."

    def add_arguments(self, parser):
        parser.add_argument("--cada", type=int, default=60, help="Segundos entre ejecuciones.")
        parser.add_argument("--una-vez", action="store_true", help="Ejecuta una sola vez y termina.")

    def handle(self, *args, **opciones):
        vivo = pathlib.Path("/tmp/tareas.vivo")
        while True:
            try:
                expiracion = servicios.expirar_vencidos()
                conciliacion = servicios.conciliar_pendientes()
                logger.info("tareas: %s %s", expiracion, conciliacion)
                vivo.touch()
            except Exception:  # noqa: BLE001  (una iteración fallida no debe tumbar el servicio)
                logger.exception("tareas: iteración fallida")
            if opciones["una_vez"]:
                return
            time.sleep(opciones["cada"])

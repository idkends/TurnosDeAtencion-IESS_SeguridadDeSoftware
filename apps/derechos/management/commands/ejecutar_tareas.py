"""Tareas programadas (vive en la capa más alta, `derechos`, porque orquesta turnos, detección y derechos): bucle simple (cada N segundos) que expira turnos y concilia con la agenda.

Es un proceso aparte (servicio `tareas` de Compose). Un fallo de una iteración se registra y no detiene el bucle.
Sin datos personales en los registros: solo contadores.
"""
import logging
import pathlib
import time

from django.core.management.base import BaseCommand

from apps.deteccion import servicios as deteccion
from apps.derechos import servicios as derechos
from apps.turnos import servicios

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Ejecuta periódicamente la expiración de turnos y la conciliación con la agenda."

    def add_arguments(self, parser):
        parser.add_argument("--cada", type=int, default=60, help="Segundos entre ejecuciones.")
        parser.add_argument("--una-vez", action="store_true", help="Ejecuta una sola vez y termina.")

    def handle(self, *args, **opciones):
        vivo = pathlib.Path("/tmp/tareas.vivo")  # nosec B108
        ultimo_vencimiento = 0.0
        while True:
            for nombre, tarea in (
                ("expiracion", servicios.expirar_vencidos),
                ("conciliacion", servicios.conciliar_pendientes),
                ("recordatorios", servicios.enviar_recordatorios),
                ("deteccion", lambda: [a.regla for a in deteccion.evaluar()]),
                ("encargados", derechos.procesar_ordenes_encargados),
            ):
                try:  # una tarea fallida no debe impedir las demás ni tumbar el servicio
                    logger.info("tareas: %s %s", nombre, tarea())
                except Exception as error:  # noqa: BLE001
                    logger.error("tareas: %s falló (%s)", nombre, type(error).__name__)
            if time.time() - ultimo_vencimiento > 86400:  # el vencimiento de datos corre una vez al día
                try:
                    logger.info("tareas: vencimiento %s", [(c.categoria, c.cantidad) for c in derechos.ejecutar_vencimiento()])
                    ultimo_vencimiento = time.time()
                except Exception as error:  # noqa: BLE001
                    logger.error("tareas: vencimiento falló (%s)", type(error).__name__)
            vivo.touch()
            if opciones["una_vez"]:
                return
            time.sleep(opciones["cada"])

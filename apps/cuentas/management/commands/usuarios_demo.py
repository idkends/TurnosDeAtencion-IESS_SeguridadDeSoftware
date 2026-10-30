"""Prepara cuentas sintéticas de afiliados con clave aleatoria conocida, para las demostraciones y el análisis dinámico (ZAP).

Imprime JSON: {"<cédula>": "<clave>"}. Las claves no se guardan en ningún archivo. Uso: manage.py usuarios_demo 3 4 5
"""
import json

from django.core.management.base import BaseCommand, CommandError

from apps.cuentas import servicios
from apps.integraciones import servicios as integraciones


class Command(BaseCommand):
    help = "Deja ACTIVAS las cuentas de afiliados sintéticos indicadas (por índice 1-50) con una clave nueva y las imprime en JSON."

    def add_arguments(self, parser):
        parser.add_argument("indices", nargs="+", type=int)

    def handle(self, *args, **opciones):
        registro = {a["indice"]: a for a in integraciones.generar_afiliados_sinteticos()}
        salida = {}
        for i in opciones["indices"]:
            if i not in registro:
                raise CommandError(f"No existe el afiliado sintético {i}.")
            clave = servicios.preparar_cuenta_demo(registro[i]["cedula"])
            if clave is None:
                raise CommandError(f"La cuenta {i} no existe; ejecute `manage.py sembrar` primero.")
            salida[registro[i]["cedula"]] = clave
        self.stdout.write(json.dumps(salida))

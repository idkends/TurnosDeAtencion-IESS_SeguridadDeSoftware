"""Regenera docs/endpoints.md desde config/inventario.py."""
import pathlib

from django.conf import settings
from django.core.management.base import BaseCommand

from config.inventario_docs import generar


class Command(BaseCommand):
    help = "Genera docs/endpoints.md a partir del inventario de endpoints."

    def handle(self, *args, **opciones):
        destino = pathlib.Path(settings.BASE_DIR) / "docs" / "endpoints.md"
        destino.write_text(generar(), encoding="utf-8", newline="\n")
        self.stdout.write(f"Escrito {destino.name}")

"""Rotación de la clave de cifrado de los secretos TOTP: recifra todo con la clave primaria (Anexo J)."""
from django.core.management.base import BaseCommand

from apps.cuentas import servicios


class Command(BaseCommand):
    help = "Recifra los secretos TOTP con la clave primaria actual (la primera de TOTP_CLAVES_CIFRADO)."

    def handle(self, *args, **opciones):
        self.stdout.write(f"Secretos recifrados: {servicios.rotar_secretos_totp()['rotados']}")

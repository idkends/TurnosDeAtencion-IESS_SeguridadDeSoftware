"""Recorre la cadena de auditoría y falla (código de salida 1) si detecta una alteración (T11b, D11)."""
from django.core.management.base import BaseCommand, CommandError

from apps.auditoria import servicios


class Command(BaseCommand):
    help = "Verifica la integridad de la cadena de auditoría (HMAC encadenado)."

    def handle(self, *args, **opciones):
        resultado = servicios.verificar_cadena()
        if not resultado.correcta:
            raise CommandError(
                f"CADENA ALTERADA en el evento {resultado.primera_falla}: {resultado.motivo} "
                f"({resultado.eventos_revisados} eventos revisados)."
            )
        self.stdout.write(self.style.SUCCESS(f"Cadena íntegra: {resultado.eventos_revisados} eventos verificados."))

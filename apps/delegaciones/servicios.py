"""Servicios de delegación (§4.4). ETAPA 1: solo la consulta de vigencia que las demás apps necesitan para autorizar.

Crear, revocar y avisar al titular se implementa en la etapa 2.
"""
from config import reloj

from .modelos import Delegacion, EstadoDelegacion


def delegacion_vigente(titular_id, representante_id) -> bool:
    """¿Existe una delegación ACTIVA y no vencida del titular al representante? Se evalúa en cada solicitud."""
    return Delegacion.objects.filter(
        titular_id=titular_id,
        representante_id=representante_id,
        estado=EstadoDelegacion.ACTIVA,
        inicio__lte=reloj.ahora(),
        vence__gt=reloj.ahora(),
    ).exists()

"""Servicios de turnos. ETAPA 1: catálogos y oferta (franjas). Las reglas RT-01 a RT-12 de la reserva llegan en la etapa 2.

Las restricciones críticas ya viven en la base (ver `modelos.py`); estos servicios son el único camino de escritura.
"""
from datetime import datetime, time, timedelta

from django.db import transaction
from django.utils import timezone as zona

from apps.auditoria import servicios as auditoria
from config import reloj
from config.parametros import P

from .modelos import Especialidad, Establecimiento, EstadoFranja, Franja

CATALOGO_ESTABLECIMIENTOS = (
    ("QUI01", "Centro de Salud Quito Norte (ficticio)"),
    ("GUA01", "Centro de Salud Guayaquil Sur (ficticio)"),
)
CATALOGO_ESPECIALIDADES = (
    ("MGEN", "Medicina general"),
    ("PEDI", "Pediatría"),
    ("GINE", "Ginecología"),
    ("ODON", "Odontología"),
    ("CARD", "Cardiología"),
)
HORAS_JORNADA = ((8, 0), (8, 30), (9, 0), (9, 30), (10, 0), (10, 30))  # 6 franjas de 30 minutos por día laborable
DURACION_FRANJA = timedelta(minutes=30)


def asegurar_catalogos() -> tuple[list[Establecimiento], list[Especialidad]]:
    """Crea (si faltan) los 2 establecimientos y las 5 especialidades de demostración. Idempotente."""
    with transaction.atomic():
        establecimientos = [
            Establecimiento.objects.get_or_create(codigo=codigo, defaults={"nombre": nombre})[0]
            for codigo, nombre in CATALOGO_ESTABLECIMIENTOS
        ]
        especialidades = [
            Especialidad.objects.get_or_create(codigo=codigo, defaults={"nombre": nombre})[0]
            for codigo, nombre in CATALOGO_ESPECIALIDADES
        ]
    return establecimientos, especialidades


def generar_franjas(establecimientos, especialidades, dias: int | None = None) -> int:
    """Genera franjas libres de lunes a viernes para los próximos `dias` días (por defecto, la ventana de reserva).

    Una franja de cada jornada queda reservada a atención prioritaria (RT-11). Idempotente por la restricción única.
    """
    dias = dias or P.turnos_dias_anticipacion
    hoy = zona.localdate(reloj.ahora())
    creadas = 0
    with transaction.atomic():
        for desplazamiento in range(1, dias + 1):
            dia = hoy + timedelta(days=desplazamiento)
            if dia.weekday() >= 5:
                continue
            for est in establecimientos:
                for esp in especialidades:
                    for indice, (hora, minuto) in enumerate(HORAS_JORNADA):
                        inicio = zona.make_aware(datetime.combine(dia, time(hora, minuto)))
                        _, nueva = Franja.objects.get_or_create(
                            establecimiento=est,
                            especialidad=esp,
                            inicio=inicio,
                            defaults={
                                "fin": inicio + DURACION_FRANJA,
                                "estado": EstadoFranja.LIBRE,
                                "cupo_prioritario": indice == len(HORAS_JORNADA) - 1,
                            },
                        )
                        creadas += nueva
        auditoria.registrar(
            accion=auditoria.Acciones.SIEMBRA,
            recurso_tipo="franja",
            detalle={"franjas_creadas": creadas, "dias": dias},
        )
    return creadas

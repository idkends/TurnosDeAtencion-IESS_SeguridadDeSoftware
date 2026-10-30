"""Derechos del titular, retención de datos y exportaciones con doble aprobación."""
import uuid

from django.db import models
from django.db.models import Q


class ConstanciaVencimiento(models.Model):
    """Constancia de cada ejecución del vencimiento de datos (eliminación segura, bloqueo o anonimización)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    categoria = models.CharField(max_length=40)
    accion = models.CharField(max_length=20)  # ELIMINACION | ANONIMIZACION | BLOQUEO
    cantidad = models.PositiveIntegerField()
    ejecutada_en = models.DateTimeField()
    corte = models.DateTimeField()  # los datos anteriores a este instante vencieron

    class Meta:
        db_table = "derechos_constancia_vencimiento"
        default_permissions = ()


class OrdenEncargado(models.Model):
    """Notificación y eliminación encargadas a un proveedor SIMULADO (plazos de 3 y 5 días, N06)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    solicitud = models.ForeignKey("cuentas.SolicitudDerechos", on_delete=models.PROTECT, related_name="ordenes_encargado")
    encargado = models.CharField(max_length=40)
    etapa = models.CharField(max_length=12)  # NOTIFICAR | ELIMINAR
    vence = models.DateTimeField()
    estado = models.CharField(max_length=10, default="PENDIENTE")
    cumplida_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "derechos_orden_encargado"
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=["solicitud", "encargado", "etapa"], name="uq_orden_encargado"),
            models.CheckConstraint(condition=Q(estado__in=["PENDIENTE", "CUMPLIDA"]), name="ck_orden_estado"),
        ]


class SolicitudExportacion(models.Model):
    """Exportación PUNTUAL de registros: requiere la aprobación de una segunda persona y se entrega una sola vez."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    solicitante = models.ForeignKey("cuentas.Usuario", on_delete=models.PROTECT, related_name="exportaciones_solicitadas")
    alcance = models.CharField(max_length=20)  # AUDITORIA
    motivo = models.CharField(max_length=200)
    desde = models.DateTimeField()
    hasta = models.DateTimeField()
    estado = models.CharField(max_length=10, default="SOLICITADA")
    aprobador = models.ForeignKey("cuentas.Usuario", null=True, blank=True, on_delete=models.PROTECT, related_name="exportaciones_aprobadas")
    creada = models.DateTimeField()
    aprobada_en = models.DateTimeField(null=True, blank=True)
    entregada_en = models.DateTimeField(null=True, blank=True)
    filas = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "derechos_solicitud_exportacion"
        default_permissions = ()
        constraints = [
            models.CheckConstraint(condition=Q(estado__in=["SOLICITADA", "APROBADA", "ENTREGADA", "VENCIDA"]), name="ck_exportacion_estado"),
            models.CheckConstraint(condition=~Q(aprobador=models.F("solicitante")), name="ck_exportacion_doble_aprobacion"),
        ]

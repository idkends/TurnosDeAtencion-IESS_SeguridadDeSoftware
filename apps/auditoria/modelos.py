"""Tabla de solo adición con cadena de hashes (R22). La escritura pasa SOLO por `auditoria.servicios.registrar`."""
import uuid

from django.db import models


class EventoAuditoria(models.Model):
    class Resultado(models.TextChoices):
        OK = "OK", "Correcto"
        DENEGADO = "DENEGADO", "Denegado"
        ERROR = "ERROR", "Error"

    secuencia = models.BigAutoField(primary_key=True)
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    ocurrido_en = models.DateTimeField()
    actor_id = models.UUIDField(null=True, blank=True)  # sin clave foránea: la auditoría no depende de otras tablas
    actor_rol = models.CharField(max_length=30, blank=True)
    accion = models.CharField(max_length=60)
    recurso_tipo = models.CharField(max_length=40, blank=True)
    recurso_id = models.CharField(max_length=64, blank=True)
    resultado = models.CharField(max_length=10, choices=Resultado.choices)
    ip = models.GenericIPAddressField(null=True, blank=True)
    correlacion = models.CharField(max_length=64, blank=True)
    detalle = models.JSONField(default=dict)
    hash_anterior = models.CharField(max_length=64)
    hash = models.CharField(max_length=64, unique=True)
    version_clave = models.PositiveSmallIntegerField()

    class Meta:
        db_table = "auditoria_evento"
        default_permissions = ()
        indexes = [
            models.Index(fields=["accion", "ocurrido_en"], name="ix_aud_accion_fecha"),
            models.Index(fields=["actor_id", "ocurrido_en"], name="ix_aud_actor_fecha"),
            models.Index(fields=["ip", "ocurrido_en"], name="ix_aud_ip_fecha"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(resultado__in=["OK", "DENEGADO", "ERROR"]), name="ck_aud_resultado"
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise RuntimeError("Los eventos de auditoría son de solo adición.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("Los eventos de auditoría son de solo adición.")


class CorreccionAuditoria(models.Model):
    """Propuesta de corrección (NO edita la cadena): se convierte en un asiento nuevo cuando la aprueba un segundo supervisor."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    secuencia_original = models.BigIntegerField()
    motivo = models.CharField(max_length=300)
    autor_id = models.UUIDField()
    estado = models.CharField(max_length=10, default="PENDIENTE")
    aprobador_id = models.UUIDField(null=True, blank=True)
    creada = models.DateTimeField()
    resuelta_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "auditoria_correccion"
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=["secuencia_original"], condition=models.Q(estado="PENDIENTE"), name="uq_correccion_pendiente"),
            models.CheckConstraint(condition=models.Q(estado__in=["PENDIENTE", "APROBADA"]), name="ck_correccion_estado"),
            models.CheckConstraint(condition=~models.Q(aprobador_id=models.F("autor_id")), name="ck_correccion_segundo_supervisor"),
        ]

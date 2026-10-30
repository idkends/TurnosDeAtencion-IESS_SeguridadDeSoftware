"""Registro de notificaciones enviadas (prueba de aviso, 6 meses). Nunca guarda el cuerpo ni secretos."""
import uuid

from django.db import models
from django.db.models import Q


class Canal(models.TextChoices):
    CORREO = "CORREO", "Correo"
    SMS = "SMS", "SMS"


class EstadoNotificacion(models.TextChoices):
    ENVIADA = "ENVIADA", "Enviada"
    FALLIDA = "FALLIDA", "Fallida"


class Notificacion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    destinatario = models.ForeignKey(
        "cuentas.Usuario", null=True, blank=True, on_delete=models.SET_NULL, related_name="notificaciones"
    )
    canal = models.CharField(max_length=10, choices=Canal.choices)
    plantilla = models.CharField(max_length=40)
    estado = models.CharField(max_length=10, choices=EstadoNotificacion.choices)
    creada = models.DateTimeField()

    class Meta:
        db_table = "notificaciones_notificacion"
        default_permissions = ()
        indexes = [models.Index(fields=["creada"], name="ix_notificacion_creada")]
        constraints = [
            models.CheckConstraint(condition=Q(canal__in=["CORREO", "SMS"]), name="ck_notif_canal"),
            models.CheckConstraint(condition=Q(estado__in=["ENVIADA", "FALLIDA"]), name="ck_notif_estado"),
        ]

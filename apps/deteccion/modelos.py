"""Alertas de detección (Anexo I). Sin datos personales: solo referencias, IP y contadores."""
import uuid

from django.db import models
from django.db.models import Q


class Severidad(models.TextChoices):
    CRITICA = "CRITICA", "Crítica"
    ALTA = "ALTA", "Alta"
    MEDIA = "MEDIA", "Media"
    INFORMATIVA = "INFORMATIVA", "Informativa"


class EstadoAlerta(models.TextChoices):
    ABIERTA = "ABIERTA", "Abierta"
    RECONOCIDA = "RECONOCIDA", "Reconocida"


class Alerta(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    regla = models.CharField(max_length=4)  # D01..D18
    severidad = models.CharField(max_length=12, choices=Severidad.choices)
    titulo = models.CharField(max_length=120)
    detalle = models.JSONField(default=dict)
    clave = models.CharField(max_length=120)  # origen + ventana: evita alertas duplicadas por el mismo hecho
    accion_automatica = models.CharField(max_length=200, blank=True)
    estado = models.CharField(max_length=12, choices=EstadoAlerta.choices, default=EstadoAlerta.ABIERTA)
    creada = models.DateTimeField()
    vence_en = models.DateTimeField(null=True, blank=True)  # plazo de atención (D16: 72 h)
    reconocida_en = models.DateTimeField(null=True, blank=True)
    reconocida_por = models.UUIDField(null=True, blank=True)
    nota = models.CharField(max_length=300, blank=True)

    class Meta:
        db_table = "deteccion_alerta"
        default_permissions = ()
        indexes = [models.Index(fields=["estado", "severidad", "creada"], name="ix_alerta_panel")]
        constraints = [
            models.UniqueConstraint(fields=["regla", "clave"], name="uq_alerta_regla_clave"),
            models.CheckConstraint(condition=Q(severidad__in=["CRITICA", "ALTA", "MEDIA", "INFORMATIVA"]), name="ck_alerta_severidad"),
            models.CheckConstraint(condition=Q(estado__in=["ABIERTA", "RECONOCIDA"]), name="ck_alerta_estado"),
        ]

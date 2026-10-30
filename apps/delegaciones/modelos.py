"""Delegación de gestión de citas a un familiar (sección 4.4). Alcance únicamente la agenda; nunca datos clínicos."""
import uuid

from django.db import models
from django.db.models import F, Q


class AlcanceDelegacion(models.TextChoices):
    AGENDA = "AGENDA", "Agenda"


class EstadoDelegacion(models.TextChoices):
    ACTIVA = "ACTIVA", "Activa"
    REVOCADA = "REVOCADA", "Revocada"


class TipoDelegacion(models.TextChoices):
    TITULAR = "TITULAR", "Autorizada por el titular"
    TUTOR_LEGAL = "TUTOR_LEGAL", "Tutor legal verificado"


class Delegacion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    titular = models.ForeignKey("cuentas.Usuario", on_delete=models.PROTECT, related_name="delegaciones_otorgadas")
    representante = models.ForeignKey(
        "cuentas.Usuario", on_delete=models.PROTECT, related_name="delegaciones_recibidas"
    )
    tipo = models.CharField(max_length=12, choices=TipoDelegacion.choices, default=TipoDelegacion.TITULAR)
    alcance = models.CharField(max_length=10, choices=AlcanceDelegacion.choices, default=AlcanceDelegacion.AGENDA)
    estado = models.CharField(max_length=10, choices=EstadoDelegacion.choices, default=EstadoDelegacion.ACTIVA)
    inicio = models.DateTimeField()
    vence = models.DateTimeField()
    revocada_en = models.DateTimeField(null=True, blank=True)
    verificada_por = models.ForeignKey(
        "cuentas.Usuario", null=True, blank=True, on_delete=models.PROTECT, related_name="delegaciones_verificadas"
    )

    class Meta:
        db_table = "delegaciones_delegacion"
        default_permissions = ()
        indexes = [models.Index(fields=["representante", "estado"], name="ix_deleg_representante")]
        constraints = [
            models.UniqueConstraint(
                fields=["titular", "representante"], condition=Q(estado="ACTIVA"), name="uq_delegacion_activa"
            ),
            models.CheckConstraint(condition=~Q(titular=F("representante")), name="ck_deleg_no_a_si_mismo"),
            models.CheckConstraint(condition=Q(alcance="AGENDA"), name="ck_deleg_solo_agenda"),
            models.CheckConstraint(condition=Q(estado__in=["ACTIVA", "REVOCADA"]), name="ck_deleg_estado"),
            models.CheckConstraint(condition=Q(vence__gt=F("inicio")), name="ck_deleg_vigencia"),
        ]

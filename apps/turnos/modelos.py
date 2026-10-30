"""Modelo de turnos. Las reglas críticas (RT-01, RT-03, RT-04, idempotencia) se imponen AQUÍ, en la base,
además de en los servicios: una solicitud manipulada o un error de la aplicación no pueden saltárselas.

Turno activo = PENDIENTE, RESERVADO o CONFIRMADO (un turno PENDIENTE ya ocupa la franja).
"""
import uuid

from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateTimeRangeField, RangeBoundary, RangeOperators
from django.db import models
from django.db.models import F, Func, Q


class TsTzRange(Func):
    """tstzrange(inicio, fin): inmutable, apta para restricciones de exclusión."""

    function = "TSTZRANGE"
    output_field = DateTimeRangeField()


class Establecimiento(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    codigo = models.CharField(max_length=10, unique=True)
    nombre = models.CharField(max_length=120)
    activo = models.BooleanField(default=True)

    class Meta:
        db_table = "turnos_establecimiento"
        default_permissions = ()

    def __str__(self) -> str:
        return self.nombre


class Especialidad(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    codigo = models.CharField(max_length=10, unique=True)
    nombre = models.CharField(max_length=80)
    activa = models.BooleanField(default=True)

    class Meta:
        db_table = "turnos_especialidad"
        default_permissions = ()

    def __str__(self) -> str:
        return self.nombre


class EstadoFranja(models.TextChoices):
    LIBRE = "LIBRE", "Libre"
    OCUPADA = "OCUPADA", "Ocupada"


class Franja(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    establecimiento = models.ForeignKey(Establecimiento, on_delete=models.PROTECT, related_name="franjas")
    especialidad = models.ForeignKey(Especialidad, on_delete=models.PROTECT, related_name="franjas")
    inicio = models.DateTimeField()
    fin = models.DateTimeField()
    estado = models.CharField(max_length=10, choices=EstadoFranja.choices, default=EstadoFranja.LIBRE)
    cupo_prioritario = models.BooleanField(default=False)  # RT-11: reservado a atención prioritaria

    class Meta:
        db_table = "turnos_franja"
        default_permissions = ()
        indexes = [models.Index(fields=["especialidad", "establecimiento", "inicio"], name="ix_franja_busqueda")]
        constraints = [
            models.CheckConstraint(condition=Q(fin__gt=F("inicio")), name="ck_franja_rango"),
            models.CheckConstraint(condition=Q(estado__in=["LIBRE", "OCUPADA"]), name="ck_franja_estado"),
            models.UniqueConstraint(fields=["establecimiento", "especialidad", "inicio"], name="uq_franja_oferta"),
        ]


class EstadoTurno(models.TextChoices):
    PENDIENTE = "PENDIENTE", "Pendiente"
    RESERVADO = "RESERVADO", "Reservado"
    CONFIRMADO = "CONFIRMADO", "Confirmado"
    ATENDIDO = "ATENDIDO", "Atendido"
    NO_ASISTIO = "NO_ASISTIO", "No asistió"
    JUSTIFICADO = "JUSTIFICADO", "Justificado"
    EXPIRADO = "EXPIRADO", "Expirado"
    CANCELADO_AFILIADO = "CANCELADO_AFILIADO", "Cancelado por el afiliado"
    CANCELADO_INSTITUCION = "CANCELADO_INSTITUCION", "Cancelado por la institución"


ESTADOS_ACTIVOS = ("PENDIENTE", "RESERVADO", "CONFIRMADO")


class MotivoTurno(models.TextChoices):
    PRIMERA_VEZ = "PRIMERA_VEZ", "Primera vez"
    CONTROL = "CONTROL", "Control"
    REFERENCIA = "REFERENCIA", "Referencia"


class Turno(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    afiliado = models.ForeignKey("cuentas.Usuario", on_delete=models.PROTECT, related_name="turnos")
    # Quien operó: el propio afiliado o su representante (los topes se aplican al afiliado atendido, RT-08).
    solicitante = models.ForeignKey("cuentas.Usuario", on_delete=models.PROTECT, related_name="turnos_solicitados")
    especialidad = models.ForeignKey(Especialidad, on_delete=models.PROTECT, related_name="turnos")
    franja = models.ForeignKey(Franja, on_delete=models.PROTECT, related_name="turnos")
    estado = models.CharField(max_length=24, choices=EstadoTurno.choices, default=EstadoTurno.PENDIENTE)
    # Copia del horario de la franja: la restricción de exclusión necesita el rango en la misma fila.
    inicio = models.DateTimeField()
    fin = models.DateTimeField()
    motivo = models.CharField(max_length=12, choices=MotivoTurno.choices, blank=True)  # sin texto libre (R02)
    reprogramaciones = models.PositiveSmallIntegerField(default=0)
    confirmar_antes_de = models.DateTimeField(null=True, blank=True)
    confirmado_en = models.DateTimeField(null=True, blank=True)
    atencion_registrada_por = models.ForeignKey(
        "cuentas.Usuario", null=True, blank=True, on_delete=models.PROTECT, related_name="atenciones_registradas"
    )
    # Cancelación (permite deshacerla durante unos minutos si el cupo sigue libre).
    cancelado_en = models.DateTimeField(null=True, blank=True)
    estado_previo_cancelacion = models.CharField(max_length=24, blank=True)
    # Inasistencia y justificación (72 h).
    inasistencia_registrada_en = models.DateTimeField(null=True, blank=True)
    justificado_en = models.DateTimeField(null=True, blank=True)
    justificacion_tipo = models.CharField(max_length=12, blank=True)  # DOCUMENTO | DECLARACION
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    @property
    def establecimiento_id(self):
        return self.franja.establecimiento_id

    class Meta:
        db_table = "turnos_turno"
        default_permissions = ()
        indexes = [
            models.Index(fields=["afiliado", "estado"], name="ix_turno_afiliado_estado"),
            models.Index(fields=["estado", "confirmar_antes_de"], name="ix_turno_vencimiento"),
        ]
        constraints = [
            # RT-01: una cita activa por afiliado y especialidad.
            models.UniqueConstraint(
                fields=["afiliado", "especialidad"],
                condition=Q(estado__in=ESTADOS_ACTIVOS),
                name="uq_turno_afiliado_especialidad",
            ),
            # RT-04: una franja, un turno.
            models.UniqueConstraint(fields=["franja"], condition=Q(estado__in=ESTADOS_ACTIVOS), name="uq_turno_franja"),
            # RT-03: sin solape de horarios para el mismo afiliado (btree_gist permite `=` sobre uuid en GiST).
            ExclusionConstraint(
                name="ex_turno_sin_solape",
                expressions=[
                    ("afiliado", RangeOperators.EQUAL),
                    (TsTzRange("inicio", "fin", RangeBoundary()), RangeOperators.OVERLAPS),
                ],
                condition=Q(estado__in=ESTADOS_ACTIVOS),
            ),
            models.CheckConstraint(condition=Q(estado__in=[e.value for e in EstadoTurno]), name="ck_turno_estado"),
            models.CheckConstraint(condition=Q(fin__gt=F("inicio")), name="ck_turno_rango"),
        ]


class SolicitudReserva(models.Model):
    """Idempotencia: un reintento con la misma clave no crea otro turno (CA-13)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    afiliado = models.ForeignKey("cuentas.Usuario", on_delete=models.PROTECT, related_name="solicitudes_reserva")
    solicitante = models.ForeignKey(
        "cuentas.Usuario", on_delete=models.PROTECT, related_name="solicitudes_reserva_enviadas"
    )
    clave_idempotencia = models.CharField(max_length=64)
    huella = models.CharField(max_length=64)  # resumen de lo solicitado: detecta reutilizar la clave con otro cuerpo
    turno = models.ForeignKey(Turno, null=True, blank=True, on_delete=models.PROTECT, related_name="solicitudes")
    creada = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "turnos_solicitud_reserva"
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=["afiliado", "clave_idempotencia"], name="uq_idempotencia"),
            models.CheckConstraint(
                condition=Q(clave_idempotencia__regex=r"^[A-Za-z0-9_-]{8,64}$"), name="ck_idempotencia_formato"
            ),
        ]


class TipoRestriccion(models.TextChoices):
    BLOQUEO_RESERVA = "BLOQUEO_RESERVA", "Bloqueo de la reserva en línea"
    TOPE_REDUCIDO = "TOPE_REDUCIDO", "Tope de turnos reducido"


class Restriccion(models.Model):
    """Consecuencia temporal de inasistencias (sección 3.5). Nunca bloquea la atención presencial."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    afiliado = models.ForeignKey("cuentas.Usuario", on_delete=models.PROTECT, related_name="restricciones")
    especialidad = models.ForeignKey(Especialidad, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    tipo = models.CharField(max_length=20, choices=TipoRestriccion.choices)
    causa = models.CharField(max_length=40)  # código fijo, visible para el afiliado
    inicio = models.DateTimeField()
    fin = models.DateTimeField()
    revertida_en = models.DateTimeField(null=True, blank=True)
    turno_origen = models.ForeignKey(Turno, null=True, blank=True, on_delete=models.SET_NULL, related_name="restricciones_derivadas")
    creada = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "turnos_restriccion"
        default_permissions = ()
        indexes = [models.Index(fields=["afiliado", "fin"], name="ix_restriccion_vigente")]
        constraints = [
            models.CheckConstraint(condition=Q(fin__gt=F("inicio")), name="ck_restriccion_rango"),
            models.CheckConstraint(condition=Q(tipo__in=[t.value for t in TipoRestriccion]), name="ck_restriccion_tipo"),
        ]


class EstadoApelacion(models.TextChoices):
    PENDIENTE = "PENDIENTE", "Pendiente"
    ACEPTADA = "ACEPTADA", "Aceptada"
    RECHAZADA = "RECHAZADA", "Rechazada"


class Apelacion(models.Model):
    """Apelación de una restricción ante una persona de la unidad (respuesta en 5 días hábiles, LOPDP art. 20)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restriccion = models.ForeignKey(Restriccion, on_delete=models.PROTECT, related_name="apelaciones")
    turno = models.ForeignKey(Turno, on_delete=models.PROTECT, related_name="apelaciones")
    afiliado = models.ForeignKey("cuentas.Usuario", on_delete=models.PROTECT, related_name="apelaciones")
    texto = models.CharField(max_length=500)
    estado = models.CharField(max_length=10, choices=EstadoApelacion.choices, default=EstadoApelacion.PENDIENTE)
    creada = models.DateTimeField()
    responder_antes_de = models.DateTimeField()
    resuelta_en = models.DateTimeField(null=True, blank=True)
    resuelta_por = models.ForeignKey(
        "cuentas.Usuario", null=True, blank=True, on_delete=models.PROTECT, related_name="apelaciones_resueltas"
    )
    motivo_resolucion = models.CharField(max_length=200, blank=True)

    class Meta:
        db_table = "turnos_apelacion"
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=["restriccion"], condition=Q(estado="PENDIENTE"), name="uq_apelacion_pendiente"),
            models.CheckConstraint(condition=Q(estado__in=["PENDIENTE", "ACEPTADA", "RECHAZADA"]), name="ck_apelacion_estado"),
        ]

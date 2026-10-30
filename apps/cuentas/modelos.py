"""Persistencia de identidad: usuarios, activación, recuperación, segundo factor y sesiones por dispositivo.

Los secretos (códigos, tokens) se guardan solo como resumen; el secreto TOTP, cifrado. Las escrituras ocurren
únicamente en `cuentas.servicios`.
"""
import uuid

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.db import models
from django.db.models import Q

from django.core.exceptions import ValidationError

from config.cedula import es_valida

from .roles import ESTADO_ETIQUETAS, ROL_ETIQUETAS, ROLES_FUNCIONARIO, EstadoCuenta, Rol


def validar_cedula(valor: str) -> None:
    if not es_valida(valor):
        raise ValidationError("Cédula no válida.")


ROL_CHOICES = [(r.value, ROL_ETIQUETAS[r]) for r in Rol]
ESTADO_CHOICES = [(e.value, ESTADO_ETIQUETAS[e]) for e in EstadoCuenta]


class Usuario(AbstractBaseUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cedula = models.CharField(max_length=10, unique=True, validators=[validar_cedula])
    nombres = models.CharField(max_length=120, blank=True)
    apellidos = models.CharField(max_length=120, blank=True)
    correo = models.EmailField(blank=True)
    telefono = models.CharField(max_length=15, blank=True)
    rol = models.CharField(max_length=20, choices=ROL_CHOICES, default=Rol.AFILIADO.value)
    estado = models.CharField(max_length=24, choices=ESTADO_CHOICES, default=EstadoCuenta.PENDIENTE_ACTIVACION.value)
    establecimiento = models.ForeignKey(
        "turnos.Establecimiento", null=True, blank=True, on_delete=models.PROTECT, related_name="funcionarios"
    )
    creado = models.DateTimeField(auto_now_add=True)
    clave_cambiada_en = models.DateTimeField(null=True, blank=True)
    # RT-11: grupos de atención prioritaria (Constitución art. 35): cupos reservados y sin restricciones automáticas.
    atencion_prioritaria = models.BooleanField(default=False)
    # Notificaciones: el SMS solo con consentimiento libre y específico (LOPDP art. 8); la oposición lo retira todo.
    consentimiento_sms = models.BooleanField(default=False)
    oposicion_recordatorios = models.BooleanField(default=False)
    contacto_cambiado_en = models.DateTimeField(null=True, blank=True)  # espera de 24 h para operaciones sensibles (Anexo F)
    # Segundo factor (TOTP): secreto cifrado con Fernet; `totp_ultimo_paso` impide reutilizar un código.
    totp_secreto_cifrado = models.TextField(blank=True)
    totp_confirmado = models.BooleanField(default=False)
    totp_ultimo_paso = models.BigIntegerField(default=0)

    objects = BaseUserManager()
    USERNAME_FIELD = "cedula"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        db_table = "cuentas_usuario"
        default_permissions = ()
        constraints = [
            models.CheckConstraint(condition=Q(rol__in=[r.value for r in Rol]), name="ck_usuario_rol"),
            models.CheckConstraint(condition=Q(estado__in=[e.value for e in EstadoCuenta]), name="ck_usuario_estado"),
        ]

    @property
    def is_active(self) -> bool:  # lo consulta ModelBackend.get_user: una cuenta suspendida pierde la sesión
        return self.estado == EstadoCuenta.ACTIVA

    @property
    def es_funcionario(self) -> bool:
        return self.rol in ROLES_FUNCIONARIO

    def __str__(self) -> str:
        return f"{self.nombres} {self.apellidos}".strip() or "Usuario"


class CodigoActivacion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    usuario = models.ForeignKey(Usuario, on_delete=models.CASCADE, related_name="codigos_activacion")
    codigo_hash = models.CharField(max_length=64)
    creado = models.DateTimeField()
    expira = models.DateTimeField()
    intentos = models.PositiveSmallIntegerField(default=0)
    usado = models.BooleanField(default=False)

    class Meta:
        db_table = "cuentas_codigo_activacion"
        default_permissions = ()
        constraints = [
            # Un solo código vigente por usuario: pedir otro invalida el anterior.
            models.UniqueConstraint(
                fields=["usuario"], condition=Q(usado=False), name="uq_codigo_activacion_vigente"
            ),
        ]


class TokenRecuperacion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    usuario = models.ForeignKey(Usuario, on_delete=models.CASCADE, related_name="tokens_recuperacion")
    token_hash = models.CharField(max_length=64, unique=True)
    creado = models.DateTimeField()
    expira = models.DateTimeField()
    usado = models.BooleanField(default=False)
    usado_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "cuentas_token_recuperacion"
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=["usuario"], condition=Q(usado=False), name="uq_token_recuperacion_vigente"),
        ]


class CodigoRecuperacionMfa(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    usuario = models.ForeignKey(Usuario, on_delete=models.CASCADE, related_name="codigos_recuperacion_mfa")
    codigo_hash = models.CharField(max_length=64, unique=True)
    usado = models.BooleanField(default=False)
    usado_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "cuentas_codigo_recuperacion_mfa"
        default_permissions = ()


class SesionDispositivo(models.Model):
    """Una fila por sesión iniciada: permite listar, limitar a 3 dispositivos y revocar de forma remota."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    usuario = models.ForeignKey(Usuario, on_delete=models.CASCADE, related_name="sesiones_dispositivo")
    session_key = models.CharField(max_length=40, unique=True)
    dispositivo_hash = models.CharField(max_length=64)
    agente = models.CharField(max_length=120, blank=True)
    ip_aproximada = models.CharField(max_length=45, blank=True)  # recortada: nunca la IP completa
    creada = models.DateTimeField()
    ultima_actividad = models.DateTimeField()
    revocada_en = models.DateTimeField(null=True, blank=True)
    motivo_revocacion = models.CharField(max_length=30, blank=True)

    class Meta:
        db_table = "cuentas_sesion_dispositivo"
        default_permissions = ()
        indexes = [models.Index(fields=["usuario", "revocada_en"], name="ix_sesion_usuario_activa")]


class TipoDerecho(models.TextChoices):
    ACCESO = "ACCESO", "Acceso"
    RECTIFICACION = "RECTIFICACION", "Rectificación"
    ELIMINACION = "ELIMINACION", "Eliminación"
    OPOSICION = "OPOSICION", "Oposición"


class SolicitudDerechos(models.Model):
    """Registro de una solicitud de derechos del titular (LOPDP arts. 12-16). Plazos: 10 y 15 días."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    titular = models.ForeignKey(Usuario, on_delete=models.PROTECT, related_name="solicitudes_derechos")
    tipo = models.CharField(max_length=14, choices=TipoDerecho.choices)
    detalle = models.CharField(max_length=500, blank=True)
    estado = models.CharField(max_length=12, default="RECIBIDA")
    creada = models.DateTimeField()
    responder_requerimiento_antes_de = models.DateTimeField()
    atender_antes_de = models.DateTimeField()
    atendida_en = models.DateTimeField(null=True, blank=True)
    atendida_por = models.ForeignKey(Usuario, null=True, blank=True, on_delete=models.PROTECT, related_name="solicitudes_derechos_atendidas")
    resultado = models.CharField(max_length=200, blank=True)

    class Meta:
        db_table = "cuentas_solicitud_derechos"
        default_permissions = ()
        constraints = [
            models.CheckConstraint(condition=Q(tipo__in=[t.value for t in TipoDerecho]), name="ck_derechos_tipo"),
            models.CheckConstraint(condition=Q(estado__in=["RECIBIDA", "EN_ATENCION", "ATENDIDA"]), name="ck_derechos_estado"),
        ]


class CambioContacto(models.Model):
    """Cambio de correo o teléfono: se confirma con un código enviado al contacto NUEVO (Anexo F)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    usuario = models.ForeignKey(Usuario, on_delete=models.CASCADE, related_name="cambios_contacto")
    campo = models.CharField(max_length=10)  # CORREO | TELEFONO
    valor_nuevo = models.CharField(max_length=120)
    codigo_hash = models.CharField(max_length=64)
    creado = models.DateTimeField()
    expira = models.DateTimeField()
    intentos = models.PositiveSmallIntegerField(default=0)
    usado = models.BooleanField(default=False)

    class Meta:
        db_table = "cuentas_cambio_contacto"
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=["usuario"], condition=Q(usado=False), name="uq_cambio_contacto_vigente"),
            models.CheckConstraint(condition=Q(campo__in=["CORREO", "TELEFONO"]), name="ck_cambio_contacto_campo"),
        ]

"""Esquemas de entrada de turnos: estrictos (campos desconocidos rechazados); el cliente no envía estado ni propietario."""
from django import forms

from config.formularios import FormularioEstricto
from config.parametros import P

ESTADOS = ["PENDIENTE", "RESERVADO", "CONFIRMADO", "ATENDIDO", "NO_ASISTIO", "JUSTIFICADO", "EXPIRADO", "CANCELADO_AFILIADO", "CANCELADO_INSTITUCION"]
_CLAVE = dict(min_length=8, max_length=64)


class _Paginado(FormularioEstricto):
    pagina = forms.IntegerField(required=False, min_value=1, max_value=10_000, initial=1)
    tamano = forms.IntegerField(required=False, min_value=1, max_value=P.paginacion_maxima)


class DisponibilidadForm(_Paginado):
    especialidad = forms.RegexField(regex=r"^[A-Z0-9]{2,10}$", required=False)
    establecimiento = forms.RegexField(regex=r"^[A-Z0-9]{2,10}$", required=False)
    fecha = forms.DateField(required=False, input_formats=["%Y-%m-%d"])


class ListaTurnosForm(_Paginado):
    afiliado = forms.UUIDField(required=False)
    estado = forms.ChoiceField(required=False, choices=[(e, e) for e in ESTADOS])


class ReservaForm(FormularioEstricto):
    franja = forms.UUIDField()
    afiliado = forms.UUIDField(required=False)
    motivo = forms.ChoiceField(required=False, choices=[("", ""), ("PRIMERA_VEZ", "Primera vez"), ("CONTROL", "Control"), ("REFERENCIA", "Referencia")])
    clave_idempotencia = forms.RegexField(regex=r"^[A-Za-z0-9_-]{8,64}$", **_CLAVE)


class ConfirmarForm(FormularioEstricto):
    """Sin campos: confirmar no necesita datos del cliente."""


class CancelarForm(FormularioEstricto):
    accion = forms.ChoiceField(required=False, choices=[("cancelar", "cancelar"), ("deshacer", "deshacer")])
    confirmar = forms.BooleanField(required=False)  # confirmación explícita antes de cancelar

    def clean(self):
        datos = super().clean()
        if (datos.get("accion") or "cancelar") == "cancelar" and not datos.get("confirmar"):
            self.add_error("confirmar", "Marque la casilla para confirmar la cancelación.")
        return datos


class ReprogramarForm(FormularioEstricto):
    franja = forms.UUIDField()
    clave_idempotencia = forms.RegexField(regex=r"^[A-Za-z0-9_-]{8,64}$", **_CLAVE)


class ApelarForm(FormularioEstricto):
    texto = forms.CharField(min_length=1, max_length=P.apelacion_texto_maximo, strip=True, widget=forms.Textarea(attrs={"rows": 4, "maxlength": P.apelacion_texto_maximo}))


class AsistenciaForm(FormularioEstricto):
    resultado = forms.ChoiceField(choices=[("ATENDIDO", "Asistió"), ("NO_ASISTIO", "No asistió"), ("JUSTIFICADO", "Justificó")])
    justificacion_tipo = forms.ChoiceField(required=False, choices=[("", ""), ("DOCUMENTO", "Documento"), ("DECLARACION", "Declaración")])


class AgendaForm(_Paginado):
    fecha = forms.DateField(required=False, input_formats=["%Y-%m-%d"])


class ContactoForm(FormularioEstricto):
    justificacion = forms.CharField(required=False, max_length=200, strip=True)


class FiltroOfertaForm(_Paginado):
    fecha = forms.DateField(required=False, input_formats=["%Y-%m-%d"])


class JornadaForm(FormularioEstricto):
    especialidad = forms.RegexField(regex=r"^[A-Z0-9]{2,10}$")
    fecha = forms.DateField(input_formats=["%Y-%m-%d"])
    desde = forms.TimeField(input_formats=["%H:%M"])
    hasta = forms.TimeField(input_formats=["%H:%M"])
    duracion = forms.IntegerField(min_value=10, max_value=120)
    ultima_prioritaria = forms.BooleanField(required=False)


class CerrarFranjaForm(FormularioEstricto):
    """Sin campos: el id va en la ruta."""


class EmergenciaForm(FormularioEstricto):
    motivo = forms.CharField(min_length=1, max_length=300, strip=True, widget=forms.Textarea(attrs={"rows": 3, "maxlength": 300}))

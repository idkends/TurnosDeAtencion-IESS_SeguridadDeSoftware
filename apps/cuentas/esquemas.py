"""Esquemas de entrada (formularios): validan forma y tamaño. Las reglas de negocio viven en `servicios.py`."""
from django import forms

from config.formularios import FormularioEstricto
from config.parametros import P

_CAMPO_CEDULA = dict(
    label="Cédula",
    max_length=10,
    min_length=10,
    strip=True,
    widget=forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "username", "pattern": "[0-9]{10}"}),
)


def _clave(label: str, autocompletar: str) -> forms.CharField:
    return forms.CharField(
        label=label,
        max_length=P.clave_longitud_maxima + 1,  # la longitud máxima se valida en el servicio con mensaje propio
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": autocompletar}, render_value=False),
    )


class _ConClaveRepetida(forms.Form):
    clave_nueva = _clave("Clave nueva", "new-password")
    clave_nueva2 = _clave("Repita la clave nueva", "new-password")

    def clean(self):
        datos = super().clean()
        if datos.get("clave_nueva") != datos.get("clave_nueva2"):
            self.add_error("clave_nueva2", "Las claves no coinciden.")
        return datos


class SolicitudActivacionForm(forms.Form):
    cedula = forms.CharField(**_CAMPO_CEDULA)


class ConfirmarActivacionForm(_ConClaveRepetida):
    cedula = forms.CharField(**_CAMPO_CEDULA)
    codigo = forms.CharField(
        label="Código de activación",
        max_length=P.activacion_digitos,
        min_length=P.activacion_digitos,
        widget=forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "one-time-code", "pattern": "[0-9]+"}),
    )


class IngresoForm(forms.Form):
    cedula = forms.CharField(**_CAMPO_CEDULA)
    clave = _clave("Clave", "current-password")


class CodigoMfaForm(forms.Form):
    codigo = forms.CharField(
        label="Código del segundo factor (o código de recuperación)",
        max_length=30,
        strip=True,
        widget=forms.TextInput(attrs={"autocomplete": "one-time-code", "inputmode": "text"}),
    )


class ReautenticarForm(CodigoMfaForm):
    clave = _clave("Clave actual", "current-password")


class CambiarClaveForm(_ConClaveRepetida):
    clave_actual = _clave("Clave actual", "current-password")


class SolicitudRecuperacionForm(forms.Form):
    cedula = forms.CharField(**_CAMPO_CEDULA)


class RestablecerClaveForm(_ConClaveRepetida):
    codigo_mfa = forms.CharField(
        label="Código del segundo factor (solo si lo tiene configurado)",
        required=False,
        max_length=30,
        widget=forms.TextInput(attrs={"autocomplete": "one-time-code"}),
    )


class ContactoCambioForm(FormularioEstricto):
    paso = forms.ChoiceField(choices=[("solicitar", "solicitar"), ("confirmar", "confirmar")])
    campo = forms.ChoiceField(required=False, choices=[("CORREO", "Correo"), ("TELEFONO", "Teléfono")])
    valor = forms.CharField(required=False, max_length=120, strip=True)
    codigo = forms.CharField(required=False, max_length=12, strip=True)


class PreferenciasForm(FormularioEstricto):
    consentimiento_sms = forms.BooleanField(required=False, label="Acepto recibir recordatorios por SMS")


class SoporteRecuperacionForm(FormularioEstricto):
    verificacion_presencial = forms.BooleanField(required=False)


class CerrarSesionForm(FormularioEstricto):
    alcance = forms.ChoiceField(required=False, choices=[("actual", "actual"), ("una", "una"), ("todas", "todas")])
    sesion = forms.UUIDField(required=False)


class DerechosForm(FormularioEstricto):
    tipo = forms.ChoiceField(label="Qué desea solicitar", choices=[("ACCESO", "Acceso a mis datos"), ("RECTIFICACION", "Rectificación"), ("ELIMINACION", "Eliminación"), ("OPOSICION", "Oposición")])
    detalle = forms.CharField(label="Detalle (opcional)", required=False, max_length=500, strip=True, widget=forms.Textarea(attrs={"rows": 3, "maxlength": 500}))


class AuditoriaForm(FormularioEstricto):
    accion = forms.RegexField(regex=r"^[A-Z_]{3,60}$", required=False)
    resultado = forms.ChoiceField(required=False, choices=[("", ""), ("OK", "OK"), ("DENEGADO", "DENEGADO"), ("ERROR", "ERROR")])
    actor = forms.UUIDField(required=False)
    desde = forms.DateField(required=False, input_formats=["%Y-%m-%d"])
    hasta = forms.DateField(required=False, input_formats=["%Y-%m-%d"])
    pagina = forms.IntegerField(required=False, min_value=1, max_value=100_000)

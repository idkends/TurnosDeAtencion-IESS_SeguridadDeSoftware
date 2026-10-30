"""Esquemas de entrada de delegaciones (estrictos)."""
from django import forms

from config.formularios import FormularioEstricto


class DelegacionForm(FormularioEstricto):
    cedula = forms.RegexField(label="Cédula del representante", regex=r"^[0-9]{10}$", max_length=10, min_length=10,
                              widget=forms.TextInput(attrs={"inputmode": "numeric", "pattern": "[0-9]{10}"}))
    paso = forms.ChoiceField(choices=[("verificar", "verificar"), ("confirmar", "confirmar")])


class RevocarForm(FormularioEstricto):
    """Sin campos: el cuerpo no puede traer datos (el id va en la ruta)."""

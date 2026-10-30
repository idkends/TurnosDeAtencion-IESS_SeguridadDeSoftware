from django import forms

from config.formularios import FormularioEstricto


class VacioForm(FormularioEstricto):
    """Sin campos: el id va en la ruta."""


class ExportacionForm(FormularioEstricto):
    motivo = forms.CharField(label="Motivo (mínimo 10 caracteres)", min_length=10, max_length=200, strip=True)
    desde = forms.DateField(label="Desde", input_formats=["%Y-%m-%d"], widget=forms.DateInput(attrs={"type": "date"}))
    hasta = forms.DateField(label="Hasta (inclusive)", input_formats=["%Y-%m-%d"], widget=forms.DateInput(attrs={"type": "date"}))


class PaginaForm(FormularioEstricto):
    pagina = forms.IntegerField(required=False, min_value=1, max_value=10_000)

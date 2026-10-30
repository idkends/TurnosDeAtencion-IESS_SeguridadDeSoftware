from django import forms

from config.formularios import FormularioEstricto


class FiltroAlertasForm(FormularioEstricto):
    severidad = forms.ChoiceField(required=False, choices=[("", ""), ("CRITICA", "Crítica"), ("ALTA", "Alta"), ("MEDIA", "Media"), ("INFORMATIVA", "Informativa")])
    estado = forms.ChoiceField(required=False, choices=[("", ""), ("ABIERTA", "Abierta"), ("RECONOCIDA", "Reconocida")])
    pagina = forms.IntegerField(required=False, min_value=1, max_value=10_000)


class ReconocerForm(FormularioEstricto):
    nota = forms.CharField(required=False, max_length=300, strip=True, widget=forms.TextInput(attrs={"maxlength": 300}))

"""Base de los esquemas de entrada: ESTRICTOS (los campos desconocidos se rechazan, API3).

El cliente no puede enviar estado, propietario ni identificadores internos: solo existen los campos declarados.
"""
from django import forms

CAMPOS_DE_FRAMEWORK = {"csrfmiddlewaretoken", "_metodo"}


class FormularioEstricto(forms.Form):
    def clean(self):
        datos = super().clean()
        extras = set(self.data.keys()) - set(self.fields) - CAMPOS_DE_FRAMEWORK
        if extras:
            raise forms.ValidationError("La solicitud contiene campos no permitidos.", code="campos_desconocidos")
        return datos

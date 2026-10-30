"""Notificaciones neutrales (§4.5, R03, I09): plantillas fijas, sin diagnóstico ni especialidad, sin secretos en el registro."""
import json

import pytest

from apps.notificaciones import servicios
from apps.notificaciones.modelos import Notificacion

pytestmark = pytest.mark.django_db
PALABRAS_PROHIBIDAS = ("diagn", "especialidad", "cardio", "pediat", "ginec", "odont", "medicina", "profesional", "médico", "tratamiento", "enfermedad")


def test_T05_todas_las_plantillas_son_neutrales():
    for nombre, (asunto, cuerpo) in servicios.PLANTILLAS.items():
        texto = (asunto + " " + cuerpo).lower()
        for palabra in PALABRAS_PROHIBIDAS:
            assert palabra not in texto, (nombre, palabra)


def test_T05_solo_los_enlaces_de_un_solo_uso_pueden_llevar_url():
    for nombre, (_, cuerpo) in servicios.PLANTILLAS.items():
        tiene_url = "{enlace}" in cuerpo or "http" in cuerpo
        assert tiene_url == (nombre in ("recuperacion", "recordatorio")), nombre  # solo enlaces de un solo uso que llevan a iniciar sesión


def test_una_plantilla_inexistente_no_se_envia():
    with pytest.raises(ValueError):
        servicios.enviar(plantilla="diagnostico_libre", canal="CORREO", destino="a@b.example")


def test_T06_el_envio_se_registra_sin_cuerpo_ni_secretos(crear_usuario):
    usuario = crear_usuario()
    assert servicios.enviar(plantilla="activacion", canal="CORREO", destino=usuario.correo,
                            parametros={"codigo": "12345678", "minutos": 15}, destinatario_id=usuario.id)
    fila = Notificacion.objects.get()
    assert (fila.plantilla, fila.estado, fila.destinatario_id) == ("activacion", "ENVIADA", usuario.id)
    assert "12345678" not in json.dumps({c.name: str(getattr(fila, c.name)) for c in Notificacion._meta.fields})
    assert "12345678" in servicios.bandeja_memoria().enviados[-1].cuerpo  # el secreto solo viaja al destinatario


def test_sin_destino_no_se_envia_nada():
    assert servicios.enviar(plantilla="clave_cambiada", canal="CORREO", destino="") is False
    assert not Notificacion.objects.exists() and servicios.bandeja_memoria().enviados == []


def test_si_el_adaptador_falla_no_se_propaga_ni_se_filtra_el_cuerpo(monkeypatch, caplog):
    def roto(self, mensaje):
        raise ConnectionError(f"fallo con {mensaje.cuerpo}")

    monkeypatch.setattr(type(servicios.bandeja_memoria()), "enviar", roto)
    assert servicios.enviar(plantilla="activacion", canal="CORREO", destino="a@b.example", parametros={"codigo": "99999999", "minutos": 15}) is False
    assert Notificacion.objects.get().estado == "FALLIDA"
    assert "99999999" not in caplog.text  # el registro no contiene el código


def test_el_adaptador_de_consola_enmascara_el_destino(capsys):
    from apps.notificaciones.adaptadores import MensajeriaConsola, Mensaje

    MensajeriaConsola().enviar(Mensaje("CORREO", "persona@correo.example", "clave_cambiada", "Asunto", "Cuerpo"))
    salida = capsys.readouterr().out
    assert "persona@correo.example" not in salida and "p***@correo.example" in salida


def test_el_adaptador_de_archivo_escribe_json_con_destino_enmascarado(tmp_path):
    from apps.notificaciones.adaptadores import MensajeriaArchivo, Mensaje

    MensajeriaArchivo(str(tmp_path)).enviar(Mensaje("SMS", "0991234567", "clave_cambiada", "A", "B"))
    (archivo,) = tmp_path.glob("*.json")
    datos = json.loads(archivo.read_text(encoding="utf-8"))
    assert datos["destino"] == "099****67" and "0991234567" not in archivo.read_text(encoding="utf-8")

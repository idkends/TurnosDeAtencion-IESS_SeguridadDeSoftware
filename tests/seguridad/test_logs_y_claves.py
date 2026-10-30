"""Logs sin secretos (R08, CWE-532), rotación de claves (Anexo J) y correcciones de auditoría con segundo supervisor."""
import io
from datetime import timedelta
import logging
import re

import pytest
from cryptography.fernet import Fernet, InvalidToken
from django.core import signing
from django.core.management import call_command
from django.test import Client

from apps.auditoria import servicios as auditoria
from apps.auditoria.modelos import EventoAuditoria
from apps.cuentas.modelos import Usuario
from config.logging_redaccion import FormateadorSeguro, redactar
from tests.ayudas import CLAVE, HTTPS, iniciar_sesion_con_mfa

pytestmark = pytest.mark.django_db


# --- Logs ------------------------------------------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "texto",
    [
        "password=Secreta12345", "clave: 'Mi clave larga'", "token=abcdef", "Authorization: Bearer abc.def-ghi", "contraseña=xyz",
        "codigo_activacion=12345678", "diagnostico=Hipertensión", "cookie=valor", "sessionid=abcd1234", "__Host-sessionid=zzzz",
        "csrfmiddlewaretoken=abc", "totp_secreto=JBSWY3DP", "cédula 1750000018 llamó", "a" * 40,
    ],
)
def test_T06_el_formateador_redacta_cada_marcador_sensible(texto):
    salida = redactar(texto)
    for secreto in ("Secreta12345", "Mi clave larga", "abcdef", "abc.def-ghi", "xyz", "12345678", "Hipertensión", "valor", "abcd1234", "zzzz",
                    "JBSWY3DP", "1750000018", "a" * 40):
        assert secreto not in salida, (texto, salida)


def test_T06_el_formateador_redacta_tambien_la_traza_de_las_excepciones():
    formateador = FormateadorSeguro("%(message)s")
    try:
        raise ValueError("falló con password=Secreta12345")
    except ValueError:
        import sys

        registro = logging.LogRecord("x", logging.ERROR, __file__, 1, "error", None, sys.exc_info())
    assert "Secreta12345" not in formateador.format(registro)


def test_el_logging_de_la_aplicacion_usa_el_formateador_seguro(settings):
    formateadores = settings.LOGGING["formatters"]
    assert any("FormateadorSeguro" in str(f.get("()", "")) for f in formateadores.values())


def test_T06_e2e_los_flujos_sensibles_no_dejan_secretos_en_los_logs(oferta, ciudadano, crear_usuario, correo_enviado):
    """Se recorre ingreso (bueno y malo), 2.º factor, recuperación y soporte con TODOS los registros capturados."""
    buffer = io.StringIO()
    manejador = logging.StreamHandler(buffer)
    manejador.setFormatter(FormateadorSeguro("%(name)s %(levelname)s %(message)s"))
    raiz = logging.getLogger()
    nivel = raiz.level
    raiz.addHandler(manejador)
    raiz.setLevel(logging.DEBUG)
    try:
        af = ciudadano(correo="titular@correo.iess-prueba.example")
        c = Client()
        c.post("/sesion", {"cedula": af.cedula, "clave": "ClaveEquivocada#9"}, **HTTPS)
        secreto = iniciar_sesion_con_mfa(c, af)
        Client().post("/cuenta/recuperar/", {"cedula": af.cedula}, **HTTPS)
        soporte = Client()
        iniciar_sesion_con_mfa(soporte, crear_usuario("SOPORTE"))
        soporte.post(f"/soporte/cuentas/{af.cedula}/recuperacion", {"verificacion_presencial": "on"}, **HTTPS)
        Client().get("/ruta-que-no-existe", **HTTPS)
        textos = [m.cuerpo for m in correo_enviado]
    finally:
        raiz.removeHandler(manejador)
        raiz.setLevel(nivel)
    registros = buffer.getvalue()
    prohibidos = [CLAVE, "ClaveEquivocada#9", secreto, af.cedula]
    for t in textos:  # tokens y códigos que viajaron en los mensajes
        prohibidos += re.findall(r"token=([\w-]+)|/([A-Za-z0-9_-]{32,})", t)[0:0]
        prohibidos += [x for x in re.findall(r"[A-Za-z0-9_-]{32,}", t)]
    assert not [p for p in prohibidos if p and p in registros]
    assert not re.search(r"(?i)(password|clave|token|diagn[oó]stico)\s*[=:]\s*(?!\[REDACTADO\])\S", registros)


# --- Rotación de claves ---------------------------------------------------------------------------------------------------------------------------------------


def test_rotacion_de_SECRET_KEY_con_fallbacks_no_invalida_lo_firmado(settings):
    firmado = signing.dumps({"a": 1}, salt="prueba")
    settings.SECRET_KEY_FALLBACKS = [settings.SECRET_KEY]
    settings.SECRET_KEY = "clave-nueva-" + "x" * 50
    assert signing.loads(firmado, salt="prueba") == {"a": 1}  # lo firmado con la clave anterior sigue valiendo
    assert signing.loads(signing.dumps({"b": 2}, salt="prueba"), salt="prueba") == {"b": 2}  # y lo nuevo se firma con la nueva
    settings.SECRET_KEY_FALLBACKS = []  # al retirar la antigua, lo viejo deja de valer
    with pytest.raises(signing.BadSignature):
        signing.loads(firmado, salt="prueba")


def test_rotacion_de_la_clave_de_cifrado_del_totp(oferta, ciudadano, settings, correo_enviado):
    af = ciudadano()
    c = Client()
    secreto = iniciar_sesion_con_mfa(c, af)
    antigua = settings.TOTP_CLAVES_CIFRADO[0]
    nueva = Fernet.generate_key().decode()
    cifrado_antes = Usuario.objects.get(pk=af.pk).totp_secreto_cifrado
    assert secreto not in cifrado_antes and Fernet(antigua.encode()).decrypt(cifrado_antes.encode()).decode() == secreto

    settings.TOTP_CLAVES_CIFRADO = [nueva, antigua]  # la nueva pasa a ser primaria y la antigua sigue descifrando
    call_command("rotar_totp")
    cifrado = Usuario.objects.get(pk=af.pk).totp_secreto_cifrado
    assert cifrado != cifrado_antes
    assert Fernet(nueva.encode()).decrypt(cifrado.encode()).decode() == secreto
    with pytest.raises(InvalidToken):
        Fernet(antigua.encode()).decrypt(cifrado.encode())

    settings.TOTP_CLAVES_CIFRADO = [nueva]  # se retira la antigua: todo sigue funcionando
    c2 = Client()
    r = c2.post("/sesion", {"cedula": af.cedula, "clave": CLAVE}, **HTTPS)
    assert r.status_code == 302 and "segundo-factor" in r["Location"]
    import pyotp
    from config import reloj

    reloj.avanzar(timedelta(seconds=90))  # otro paso TOTP: el código anterior ya se usó
    r = c2.post(r["Location"], {"codigo": pyotp.TOTP(secreto).at(int(reloj.ahora().timestamp()))}, **HTTPS)
    assert r.status_code == 302 and "segundo-factor" not in r["Location"]  # el secreto recifrado sigue generando códigos válidos
    assert EventoAuditoria.objects.filter(accion="CLAVE_ROTADA", detalle__rotados=1).exists()
    call_command("rotar_totp")  # idempotente
    assert Usuario.objects.get(pk=af.pk).totp_secreto_cifrado == cifrado


def test_rotacion_de_la_clave_de_auditoria_y_deteccion_de_una_clave_retirada(settings):
    auditoria.registrar(accion="PRUEBA")
    settings.AUDITORIA_VERSION_ACTUAL = 2
    auditoria.registrar(accion="PRUEBA")
    assert auditoria.verificar_cadena().correcta  # conviven eventos firmados con v1 y con v2
    claves = dict(settings.AUDITORIA_CLAVES)
    settings.AUDITORIA_CLAVES = {2: claves[2]}  # alguien retira la v1 sin archivar antes
    r = auditoria.verificar_cadena()
    assert not r.correcta and r.motivo == "versión de clave desconocida"
    resultado = auditoria.verificar_y_registrar()
    assert not resultado.correcta
    assert EventoAuditoria.objects.filter(accion="VERIFICACION_AUDITORIA_FALLIDA").exists()  # queda rastro para la regla D11


# --- Correcciones de auditoría -------------------------------------------------------------------------------------------------------------------------------


def test_una_correccion_es_un_asiento_nuevo_con_motivo_autor_y_segundo_supervisor(crear_usuario):
    a, b = crear_usuario("AUDITOR_DPD"), crear_usuario("AUDITOR_DPD")
    original = auditoria.registrar(accion="PRUEBA", detalle={"dato": "erróneo"})
    antes = EventoAuditoria.objects.count()
    with pytest.raises(auditoria.CorreccionInvalida):
        auditoria.proponer_correccion(autor_id=a.id, autor_rol="AUDITOR_DPD", secuencia=original.secuencia, motivo="corto")
    with pytest.raises(auditoria.CorreccionInvalida):
        auditoria.proponer_correccion(autor_id=a.id, autor_rol="AUDITOR_DPD", secuencia=999999, motivo="Motivo suficientemente largo")
    c = auditoria.proponer_correccion(autor_id=a.id, autor_rol="AUDITOR_DPD", secuencia=original.secuencia, motivo="Se registró mal el dato del titular")
    with pytest.raises(auditoria.CorreccionInvalida):  # solo una pendiente por evento
        auditoria.proponer_correccion(autor_id=b.id, autor_rol="AUDITOR_DPD", secuencia=original.secuencia, motivo="Otro motivo igualmente largo")
    with pytest.raises(auditoria.CorreccionInvalida):  # el autor no puede aprobar su propia corrección
        auditoria.aprobar_correccion(aprobador_id=a.id, aprobador_rol="AUDITOR_DPD", correccion_id=c.id)
    asiento = auditoria.aprobar_correccion(aprobador_id=b.id, aprobador_rol="AUDITOR_DPD", correccion_id=c.id)
    assert asiento.accion == "CORRECCION_AUDITORIA" and asiento.secuencia > original.secuencia
    assert asiento.detalle["autor_id"] == str(a.id) and asiento.detalle["aprobador_id"] == str(b.id) and "erróneo" not in str(asiento.detalle)
    assert EventoAuditoria.objects.get(secuencia=original.secuencia).detalle == {"dato": "erróneo"}  # el original NO se tocó
    assert EventoAuditoria.objects.count() == antes + 2  # propuesta + corrección: solo se agregan filas
    with pytest.raises(auditoria.CorreccionInvalida):  # ya resuelta
        auditoria.aprobar_correccion(aprobador_id=b.id, aprobador_rol="AUDITOR_DPD", correccion_id=c.id)
    assert auditoria.verificar_cadena().correcta

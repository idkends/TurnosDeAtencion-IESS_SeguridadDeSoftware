"""Activación de cuenta (Anexo F, CA-04, CA-05): código de un solo uso enviado SOLO al contacto registrado."""
import re
import time
from datetime import timedelta

import pytest

from apps.cuentas import servicios
from apps.cuentas.modelos import CodigoActivacion, Usuario
from config import reloj
from config.cedula import sintetica

pytestmark = pytest.mark.django_db


def _ultimo_codigo(correo_enviado):
    return re.search(r"código de activación es (\d{8})", correo_enviado[-1].cuerpo).group(1)


def test_el_codigo_va_al_contacto_registrado_y_tiene_8_digitos(afiliar, correo_enviado):
    datos = afiliar()
    servicios.solicitar_activacion(datos.cedula)
    assert len(correo_enviado) == 1
    assert correo_enviado[0].destino == datos.correo  # el de afiliación, nunca uno escrito por quien solicita
    assert len(_ultimo_codigo(correo_enviado)) == 8


def test_CA04_no_hay_forma_de_indicar_otro_contacto(afiliar, correo_enviado):
    """La firma del servicio ni siquiera acepta un correo: el pre-secuestro de cuenta no es expresable."""
    import inspect

    assert list(inspect.signature(servicios.solicitar_activacion).parameters) == ["cedula_cruda"]
    datos = afiliar(correo="titular@correo.iess-prueba.example")
    servicios.solicitar_activacion(datos.cedula)
    assert [m.destino for m in correo_enviado] == ["titular@correo.iess-prueba.example"]


def test_activacion_completa_deja_la_cuenta_activa_con_argon2id(afiliar, correo_enviado, clave_buena):
    datos = afiliar()
    servicios.solicitar_activacion(datos.cedula)
    servicios.activar_cuenta(datos.cedula, _ultimo_codigo(correo_enviado), clave_buena)
    usuario = Usuario.objects.get(cedula=datos.cedula)
    assert usuario.estado == "ACTIVA"
    assert usuario.password.startswith("argon2$argon2id$")
    assert usuario.check_password(clave_buena)
    assert (usuario.correo, usuario.nombres) == (datos.correo, datos.nombres)  # copiados de afiliación


def test_T03_activacion_no_reutilizable(afiliar, correo_enviado, clave_buena):
    datos = afiliar()
    servicios.solicitar_activacion(datos.cedula)
    codigo = _ultimo_codigo(correo_enviado)
    servicios.activar_cuenta(datos.cedula, codigo, clave_buena)
    with pytest.raises(servicios.CodigoInvalido):  # el mismo código otra vez
        servicios.activar_cuenta(datos.cedula, codigo, "Otra-Clave-Larga-482")
    assert Usuario.objects.get(cedula=datos.cedula).check_password(clave_buena)  # la clave no cambió


def test_el_codigo_se_invalida_tras_5_intentos_fallidos(afiliar, correo_enviado, clave_buena):
    datos = afiliar()
    servicios.solicitar_activacion(datos.cedula)
    bueno = _ultimo_codigo(correo_enviado)
    malo = "00000000" if bueno != "00000000" else "11111111"
    for _ in range(5):
        with pytest.raises(servicios.CodigoInvalido):
            servicios.activar_cuenta(datos.cedula, malo, clave_buena)
    with pytest.raises(servicios.CodigoInvalido):  # ya ni el código correcto sirve
        servicios.activar_cuenta(datos.cedula, bueno, clave_buena)
    assert Usuario.objects.get(cedula=datos.cedula).estado == "PENDIENTE_ACTIVACION"


def test_el_codigo_vence_a_los_15_minutos(afiliar, correo_enviado, clave_buena):
    datos = afiliar()
    servicios.solicitar_activacion(datos.cedula)
    codigo = _ultimo_codigo(correo_enviado)
    reloj.avanzar(timedelta(minutes=15, seconds=1))
    with pytest.raises(servicios.CodigoInvalido):
        servicios.activar_cuenta(datos.cedula, codigo, clave_buena)


def test_pedir_otro_codigo_invalida_el_anterior(afiliar, correo_enviado, clave_buena):
    datos = afiliar()
    servicios.solicitar_activacion(datos.cedula)
    primero = _ultimo_codigo(correo_enviado)
    servicios.solicitar_activacion(datos.cedula)
    segundo = _ultimo_codigo(correo_enviado)
    assert CodigoActivacion.objects.filter(usado=False).count() == 1
    if primero != segundo:
        with pytest.raises(servicios.CodigoInvalido):
            servicios.activar_cuenta(datos.cedula, primero, clave_buena)


def test_el_codigo_no_se_guarda_en_claro(afiliar, correo_enviado):
    datos = afiliar()
    servicios.solicitar_activacion(datos.cedula)
    codigo = _ultimo_codigo(correo_enviado)
    fila = CodigoActivacion.objects.get()
    assert codigo not in fila.codigo_hash and len(fila.codigo_hash) == 64


def test_T03_clave_biografica_rechazada_y_el_codigo_no_se_consume(afiliar, correo_enviado, clave_buena):
    datos = afiliar()  # nacida el 14/03/1988
    servicios.solicitar_activacion(datos.cedula)
    codigo = _ultimo_codigo(correo_enviado)
    with pytest.raises(servicios.ClaveRechazada) as error:
        servicios.activar_cuenta(datos.cedula, codigo, "Mi-14031988-clave")
    assert any("fecha de nacimiento" in p for p in error.value.problemas)
    servicios.activar_cuenta(datos.cedula, codigo, clave_buena)  # el mismo código sigue sirviendo con una clave buena


def test_CA05_cedula_inexistente_indistinguible_de_una_existente(afiliar, correo_enviado):
    existente = afiliar()
    inexistente = sintetica(777777)
    assert servicios.solicitar_activacion(existente.cedula) is None
    assert servicios.solicitar_activacion(inexistente) is None  # mismo retorno, sin excepción
    assert len(correo_enviado) == 1  # solo la existente generó un mensaje
    assert not Usuario.objects.filter(cedula=inexistente).exists()  # y no se crea nada


def test_CA05_el_tiempo_de_respuesta_no_delata_si_la_cedula_existe(afiliar):
    existente = afiliar()
    inexistente = sintetica(777778)

    def medir(cedula):
        inicio = time.monotonic()
        servicios.solicitar_activacion(cedula)
        return time.monotonic() - inicio

    con = sorted(medir(existente.cedula) for _ in range(3))[1]
    sin = sorted(medir(inexistente) for _ in range(3))[1]
    assert abs(con - sin) < 0.08, (con, sin)


def test_afiliado_no_vigente_no_recibe_codigo(afiliar, correo_enviado):
    datos = afiliar(vigente=False)
    servicios.solicitar_activacion(datos.cedula)
    assert correo_enviado == [] and not Usuario.objects.filter(cedula=datos.cedula).exists()


def test_si_afiliacion_cae_no_se_envia_nada_y_no_se_revela(afiliar, correo_enviado):
    from apps.integraciones import servicios as integraciones

    datos = afiliar()
    integraciones.afiliacion_memoria().caido = True
    assert servicios.solicitar_activacion(datos.cedula) is None
    assert correo_enviado == []


def test_cuenta_ya_activa_no_recibe_nuevo_codigo(afiliar, crear_usuario, correo_enviado):
    datos = afiliar()
    crear_usuario(cedula=datos.cedula)
    servicios.solicitar_activacion(datos.cedula)
    assert correo_enviado == []


def test_limite_de_solicitudes_de_activacion_por_cedula(afiliar, parametro):
    datos = afiliar()
    for _ in range(5):
        servicios.solicitar_activacion(datos.cedula)
    with pytest.raises(servicios.LimiteExcedido):
        servicios.solicitar_activacion(datos.cedula)


def test_formato_de_cedula_invalido_no_hace_nada(correo_enviado):
    servicios.solicitar_activacion("no-es-una-cedula")
    servicios.solicitar_activacion("1234567890")  # 10 dígitos pero dígito verificador incorrecto
    assert correo_enviado == []

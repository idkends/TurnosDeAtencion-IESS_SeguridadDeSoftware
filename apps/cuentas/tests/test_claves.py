"""T03: calidad de claves (longitud, filtradas, biográficas) y Argon2id."""
from datetime import date

import pytest

from apps.cuentas import claves
from config.cedula import sintetica

NACIMIENTO = date(1988, 3, 14)
BIO = dict(cedula=sintetica(5), nombres="Ana María", apellidos="Quishpe Lara", correo="ana.quishpe@correo.iess-prueba.example", telefono="0991234567", fecha_nacimiento=NACIMIENTO)


@pytest.mark.parametrize(
    "clave",
    [
        "14031988xxxx",  # ddmmyyyy
        "xx19880314xx",  # yyyymmdd
        "Hola-14-03-1988",  # con separadores
        "03141988-Hola",  # mmddyyyy
        "mi140388clave",  # ddmmyy
    ],
)
def test_T03_clave_basada_en_la_fecha_de_nacimiento_rechazada(clave):
    problemas = claves.validar(clave, **BIO)
    assert any("fecha de nacimiento" in p for p in problemas), problemas


def test_clave_con_la_cedula_rechazada():
    assert claves.validar(f"abc{BIO['cedula']}xyz", **BIO)


def test_clave_con_el_nombre_rechazada():
    assert claves.validar("quishpe-Verde-9988", **BIO)


def test_clave_con_el_telefono_rechazada():
    assert claves.validar("Azul-1234567-Rio", **BIO)


@pytest.mark.parametrize("clave", ["corta1!", "Abc123!", "sinoctoc"])
def test_clave_menor_a_12_caracteres_rechazada(clave):
    assert any("12 caracteres" in p for p in claves.validar(clave, **BIO))


@pytest.mark.parametrize("clave", ["password1234", "123456789012", "qwertyuiop123", "Bienvenido2024", "iloveyou1234"])
def test_clave_filtrada_rechazada(clave):
    assert any("común" in p or "filtraciones" in p for p in claves.validar(clave, **BIO)), clave


def test_clave_de_un_solo_caracter_repetido_rechazada():
    assert claves.validar("aaaaaaaaaaaaaaaa", **BIO)


def test_clave_demasiado_larga_rechazada():
    assert any("superar" in p for p in claves.validar("x" * 500, **BIO))


def test_clave_buena_aceptada():
    assert claves.validar("Zorro-Azul-Lluvia-731", **BIO) == []


def test_las_claves_se_guardan_con_argon2id(crear_usuario):
    usuario = crear_usuario()
    assert usuario.password.startswith("argon2$argon2id$"), usuario.password[:20]


def test_en_produccion_el_unico_hasher_es_argon2id():
    from config.settings import base

    assert base.PASSWORD_HASHERS == ["django.contrib.auth.hashers.Argon2PasswordHasher"]

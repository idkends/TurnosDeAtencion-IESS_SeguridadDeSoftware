"""Fixtures compartidas. Las pruebas pueden usar el ORM directamente para preparar datos; el código de producción no."""
import itertools
from datetime import date

import pytest
from django.core.cache import caches
from django.db import connections
from django.test import testcases

from apps.cuentas.modelos import Usuario
from apps.integraciones import servicios as integraciones
from apps.integraciones.servicios import DatosAfiliacion
from apps.notificaciones import servicios as notificaciones
from config import contexto, reloj
from config.cedula import sintetica
from config.parametros import P

CLAVE_BUENA = "Zorro-Azul-Lluvia-731"  # 21 caracteres, no biográfica, no filtrada
_contador = itertools.count(100)

# --- Pruebas con transacciones reales (hilos) -------------------------------------------------------------------------------
# La auditoría rechaza TRUNCATE (disparador). El vaciado de la base de PRUEBAS entre pruebas transaccionales
# lo necesita, así que solo ahí, en la base temporal y con su dueño, se desactiva ese disparador unos instantes.
_teardown_original = testcases.TransactionTestCase._fixture_teardown


def _teardown_con_auditoria(self):
    alias = list(self._databases_names(include_mirrors=False))
    for a in alias:
        with connections[a].cursor() as cursor:
            cursor.execute("ALTER TABLE auditoria_evento DISABLE TRIGGER auditoria_sin_truncate")
    try:
        _teardown_original(self)
    finally:
        for a in alias:
            with connections[a].cursor() as cursor:
                cursor.execute("ALTER TABLE auditoria_evento ENABLE TRIGGER auditoria_sin_truncate")


testcases.TransactionTestCase._fixture_teardown = _teardown_con_auditoria


@pytest.fixture(autouse=True)
def entorno_limpio():
    """Cada prueba parte de caché, reloj, buzón y afiliación en blanco."""
    for nombre in ("default", "limites"):
        caches[nombre].clear()
    reloj.restablecer()
    notificaciones.bandeja_memoria().limpiar()
    integraciones.afiliacion_memoria().limpiar()
    token = contexto.fijar(ip="203.0.113.7", dispositivo="")
    yield
    contexto.restablecer(token)
    reloj.restablecer()


@pytest.fixture
def parametro():
    """Cambia un parámetro de configuración durante una prueba y lo restaura al terminar."""
    originales = {}

    def _fijar(nombre, valor):
        originales.setdefault(nombre, getattr(P, nombre))
        object.__setattr__(P, nombre, valor)

    yield _fijar
    for nombre, valor in originales.items():
        object.__setattr__(P, nombre, valor)


@pytest.fixture
def clave_buena():
    return CLAVE_BUENA


@pytest.fixture
def correo_enviado():
    """Devuelve los mensajes simulados enviados hasta el momento."""
    return notificaciones.bandeja_memoria().enviados


@pytest.fixture
def afiliar():
    """Registra una persona en el servicio de afiliación simulado (fuente de verdad del contacto)."""

    def _afiliar(cedula=None, *, vigente=True, correo=None, nacimiento=date(1988, 3, 14), nombres="Ana María", apellidos="Quishpe Lara"):
        cedula = cedula or sintetica(next(_contador))
        datos = DatosAfiliacion(
            cedula=cedula,
            nombres=nombres,
            apellidos=apellidos,
            correo=correo or f"u{cedula}@correo.iess-prueba.example",
            telefono="0990000001",
            vigente=vigente,
            fecha_nacimiento=nacimiento,
        )
        integraciones.afiliacion_memoria().agregar(datos)
        return datos

    return _afiliar


@pytest.fixture
def crear_usuario(db):
    """Crea una cuenta ACTIVA directamente en la base (preparación de pruebas)."""

    def _crear(rol="AFILIADO", *, clave=CLAVE_BUENA, cedula=None, estado="ACTIVA", **extra):
        cedula = cedula or sintetica(next(_contador))
        usuario = Usuario(
            cedula=cedula,
            rol=rol,
            estado=estado,
            nombres=extra.pop("nombres", "Pedro"),
            apellidos=extra.pop("apellidos", "Prueba Uno"),
            correo=extra.pop("correo", f"u{cedula}@correo.iess-prueba.example"),
            telefono=extra.pop("telefono", "0990000002"),
            **extra,
        )
        usuario.set_password(clave)
        usuario.save()
        return usuario

    return _crear

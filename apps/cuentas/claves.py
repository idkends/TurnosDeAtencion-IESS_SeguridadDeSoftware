"""Reglas de calidad de claves (Anexo E, T03): longitud, claves filtradas y claves biográficas.

Sin reglas de composición arbitrarias (NIST SP 800-63B): se exige longitud y se rechaza lo predecible.
Es un módulo interno de `cuentas`; el resto del sistema usa `cuentas.servicios`.
"""
import re
import unicodedata
from datetime import date
from functools import lru_cache

from django.conf import settings

from config.parametros import P


@lru_cache(maxsize=1)
def _filtradas() -> frozenset[str]:
    try:
        with open(settings.CLAVES_FILTRADAS_ARCHIVO, encoding="utf-8") as archivo:
            return frozenset(linea.strip().lower() for linea in archivo if linea.strip())
    except FileNotFoundError:  # sin lista no se puede garantizar el control: falla cerrado
        raise RuntimeError("Falta la lista local de claves filtradas.") from None


def _normalizar(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[\s\-_./,:;]+", "", sin_tildes.lower())


def _fechas(nacimiento: date) -> set[str]:
    d, m, a = f"{nacimiento.day:02d}", f"{nacimiento.month:02d}", f"{nacimiento.year:04d}"
    d1, m1 = str(nacimiento.day), str(nacimiento.month)
    return {
        d + m + a, a + m + d, m + d + a, d + m + a[2:], a[2:] + m + d, m + d + a[2:],
        d1 + m1 + a, a + m1 + d1, d1 + m + a, d + m1 + a,
    }


def validar(
    clave: str,
    *,
    cedula: str = "",
    nombres: str = "",
    apellidos: str = "",
    correo: str = "",
    telefono: str = "",
    fecha_nacimiento: date | None = None,
) -> list[str]:
    """Devuelve la lista de problemas (vacía si la clave es aceptable). Los mensajes son para la persona usuaria."""
    problemas: list[str] = []
    if len(clave) < P.clave_longitud_minima:
        problemas.append(f"Debe tener al menos {P.clave_longitud_minima} caracteres.")
    if len(clave) > P.clave_longitud_maxima:
        problemas.append(f"No debe superar los {P.clave_longitud_maxima} caracteres.")
        return problemas
    minuscula = clave.lower()
    sin_cola = re.sub(r"[\d\W_]+$", "", minuscula)
    if minuscula in _filtradas() or (sin_cola and sin_cola in _filtradas()) or len(set(clave)) <= 2:
        problemas.append("Es una clave muy común o ha aparecido en filtraciones; elija otra.")

    plano = _normalizar(clave)
    personales = False
    if cedula and (cedula in plano or cedula[:-1] in plano):
        personales = True
    if fecha_nacimiento and any(f in plano for f in _fechas(fecha_nacimiento)):
        personales = True
    for palabra in re.split(r"\s+", f"{nombres} {apellidos}".strip()):
        palabra = _normalizar(palabra)
        if len(palabra) >= 4 and palabra in plano:
            personales = True
    local = _normalizar(correo.split("@")[0]) if correo else ""
    if len(local) >= 5 and local in plano:
        personales = True
    digitos_tel = re.sub(r"\D", "", telefono)
    if len(digitos_tel) >= 7 and digitos_tel[-7:] in re.sub(r"\D", "", plano):
        personales = True
    if personales:
        problemas.append("No debe contener su cédula, su nombre, su teléfono ni su fecha de nacimiento.")
    return problemas

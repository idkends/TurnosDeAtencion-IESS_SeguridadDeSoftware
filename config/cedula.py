"""Validación y generación sintética de cédulas ecuatorianas (módulo 10). Sin dependencias de Django."""

_COEFICIENTES = (2, 1, 2, 1, 2, 1, 2, 1, 2)


def digito_verificador(nueve_digitos: str) -> int:
    total = 0
    for digito, coef in zip(nueve_digitos, _COEFICIENTES):
        producto = int(digito) * coef
        total += producto - 9 if producto > 9 else producto
    return (10 - total % 10) % 10


def es_valida(cedula: str) -> bool:
    if not (isinstance(cedula, str) and len(cedula) == 10 and cedula.isascii() and cedula.isdigit()):
        return False
    provincia = int(cedula[:2])
    if not (1 <= provincia <= 24 or provincia == 30):
        return False
    if int(cedula[2]) > 5:  # persona natural
        return False
    return digito_verificador(cedula[:9]) == int(cedula[9])


def sintetica(indice: int, provincia: int = 17) -> str:
    """Cédula FICTICIA con formato válido, determinista por índice (no corresponde a nadie real a propósito)."""
    base = f"{provincia:02d}5{indice:06d}"
    return base + str(digito_verificador(base))

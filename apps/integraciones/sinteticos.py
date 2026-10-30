"""Datos sintéticos de afiliación: 100 % ficticios y deterministas. Sin dependencias de Django.

Lo usan el servicio de afiliación simulado (fuente de verdad del contacto registrado) y la siembra.
"""
from datetime import date

from config.cedula import sintetica

_NOMBRES = (
    "Ana", "Luis", "María", "Carlos", "Lucía", "Jorge", "Elena", "Pablo", "Sofía", "Diego",
    "Camila", "Andrés", "Valeria", "Mateo", "Paula", "Esteban", "Daniela", "Iván", "Gabriela", "Marco",
)
_APELLIDOS = (
    "Andrade", "Benítez", "Cárdenas", "Delgado", "Espinoza", "Flores", "Guerrero", "Herrera", "Ibarra", "Jaramillo",
    "Lara", "Mejía", "Naranjo", "Ortiz", "Paredes", "Quishpe", "Reyes", "Salazar", "Tapia", "Vega",
)
DOMINIO_CORREO = "correo.iess-prueba.example"  # dominio reservado (RFC 2606): nunca resuelve a un buzón real
TOTAL_AFILIADOS = 51  # 1..50 afiliados y el 51 es el representante de demostración
INDICE_REPRESENTANTE = 51
INDICES_NO_VIGENTES = frozenset({49, 50})


def generar_afiliados(total: int = TOTAL_AFILIADOS) -> list[dict]:
    afiliados = []
    for i in range(1, total + 1):
        nombres = f"{_NOMBRES[i % len(_NOMBRES)]} {_NOMBRES[(i * 7 + 3) % len(_NOMBRES)]}"
        apellidos = f"{_APELLIDOS[(i * 3) % len(_APELLIDOS)]} {_APELLIDOS[(i * 5 + 1) % len(_APELLIDOS)]}"
        nacimiento = date(1950 + (i * 13) % 62, 1 + (i * 5) % 12, 1 + (i * 11) % 28)
        afiliados.append(
            {
                "indice": i,
                "cedula": sintetica(i),
                "nombres": nombres,
                "apellidos": apellidos,
                "correo": f"afiliado{i:03d}@{DOMINIO_CORREO}",
                "telefono": f"0990{i:06d}",
                "vigente": i not in INDICES_NO_VIGENTES,
                "fecha_nacimiento": nacimiento.isoformat(),
            }
        )
    return afiliados

"""Afiliación en memoria (pruebas): permite simular datos, ausencia y caída del tercero."""
from ..puertos import DatosAfiliacion, ServicioNoDisponible


class AfiliacionMemoria:
    def __init__(self):
        self.registros: dict[str, DatosAfiliacion] = {}
        self.caido = False
        self.consultas = 0

    def agregar(self, datos: DatosAfiliacion) -> None:
        self.registros[datos.cedula] = datos

    def consultar(self, cedula: str) -> DatosAfiliacion | None:
        self.consultas += 1
        if self.caido:
            raise ServicioNoDisponible("simulado: servicio caído")
        return self.registros.get(cedula)

    def limpiar(self) -> None:
        self.registros.clear()
        self.caido = False
        self.consultas = 0

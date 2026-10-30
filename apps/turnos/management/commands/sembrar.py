"""manage.py sembrar: datos 100 % sintéticos para la demostración local.

Crea 2 establecimientos, 5 especialidades, franjas de 30 días, 50 afiliados y 1 representante (pendientes de
activación: se activan con el código que el servicio de afiliación simulado envía a su contacto registrado) y el
personal: recepción, profesional, soporte, administración de oferta, auditor/DPD y administrador de TI.

Las claves de los funcionarios se generan al azar y se muestran UNA vez; no se guardan en ningún archivo.
"""
import secrets

from django.core.management.base import BaseCommand

from apps.cuentas import servicios as cuentas
from apps.cuentas.politicas import Rol
from apps.integraciones import servicios as integraciones
from apps.turnos import servicios as turnos
from config.cedula import sintetica

PERSONAL = (
    # (índice de cédula, rol, nombres, apellidos, índice de establecimiento o None)
    (901, Rol.RECEPCION, "Rosa", "Recepción Norte", 0),
    (902, Rol.RECEPCION, "Raúl", "Recepción Sur", 1),
    (903, Rol.PROFESIONAL, "Pedro", "Profesional Norte", 0),
    (904, Rol.SOPORTE, "Sara", "Soporte Técnico", None),
    (905, Rol.ADMIN_OFERTA, "Olga", "Administración Oferta", 0),
    (906, Rol.AUDITOR_DPD, "Aldo", "Auditoría DPD", None),
    (907, Rol.ADMIN_TI, "Tomás", "Administración TI", None),
)
INDICES_DEMO_ACTIVOS = (1, 2, 51)  # dos afiliados y el representante


def _clave_aleatoria() -> str:
    return secrets.token_urlsafe(15)


class Command(BaseCommand):
    help = "Siembra datos sintéticos de demostración (idempotente)."

    def add_arguments(self, parser):
        parser.add_argument("--dias", type=int, default=None, help="Días de franjas a generar (por defecto, la ventana de reserva).")
        parser.add_argument("--sin-demo", action="store_true", help="No activar las cuentas de demostración de afiliados.")

    def handle(self, *args, **opciones):
        establecimientos, especialidades = turnos.asegurar_catalogos()
        franjas = turnos.generar_franjas(establecimientos, especialidades, dias=opciones["dias"])
        self.stdout.write(f"Establecimientos: {len(establecimientos)} · Especialidades: {len(especialidades)} · Franjas nuevas: {franjas}")

        registro = integraciones.generar_afiliados_sinteticos()
        for a in registro:
            if not opciones["sin_demo"] and a["indice"] in INDICES_DEMO_ACTIVOS:
                continue  # estas cuentas se crean ya activas más abajo
            cuentas.precargar_afiliado(
                cedula=a["cedula"], nombres=a["nombres"], apellidos=a["apellidos"], correo=a["correo"], telefono=a["telefono"],
                rol=Rol.REPRESENTANTE.value if a["indice"] == 51 else Rol.AFILIADO.value,
            )
        self.stdout.write(f"Afiliados y representante precargados (pendientes de activación): {len(registro)}")

        entregadas = []
        for indice, rol, nombres, apellidos, est in PERSONAL:
            cedula = sintetica(indice)
            clave = _clave_aleatoria()
            _, creada = cuentas.aprovisionar_cuenta(
                cedula=cedula, rol=rol.value, nombres=nombres, apellidos=apellidos,
                correo=f"funcionario{indice}@correo.iess-prueba.example", telefono=f"0991{indice:06d}", clave=clave,
                establecimiento_id=establecimientos[est].id if est is not None else None,
            )
            entregadas.append((cedula, rol.value, clave if creada else "(ya existía: clave sin cambios)"))
        if not opciones["sin_demo"]:
            for a in registro:
                if a["indice"] in INDICES_DEMO_ACTIVOS:
                    clave = _clave_aleatoria()
                    _, creada = cuentas.aprovisionar_cuenta(
                        cedula=a["cedula"], rol=Rol.REPRESENTANTE.value if a["indice"] == 51 else Rol.AFILIADO.value,
                        nombres=a["nombres"], apellidos=a["apellidos"], correo=a["correo"], telefono=a["telefono"], clave=clave,
                    )
                    if creada:
                        entregadas.append((a["cedula"], "AFILIADO/REPRESENTANTE (demo)", clave))
        self.stdout.write("\nCredenciales sintéticas (se muestran una sola vez; los funcionarios deben configurar su 2.º factor al ingresar):")
        for cedula, rol, clave in entregadas:
            self.stdout.write(f"  {cedula}  {rol:<32} {clave}")
        self.stdout.write(self.style.SUCCESS("Siembra completada."))

"""manage.py sembrar: datos sintéticos, idempotente, con el servicio de afiliación como fuente del contacto."""
import io
import re
from datetime import timedelta

import pytest
from django.core.management import call_command

from apps.cuentas.modelos import Usuario
from apps.integraciones.sinteticos import generar_afiliados
from apps.turnos.modelos import Especialidad, Establecimiento, Franja
from config import reloj
from config.cedula import es_valida

pytestmark = pytest.mark.django_db


@pytest.fixture
def sembrado():
    salida = io.StringIO()
    call_command("sembrar", stdout=salida)
    return salida.getvalue()


def test_la_siembra_crea_la_oferta_pedida(sembrado):
    assert Establecimiento.objects.count() == 2 and Especialidad.objects.count() == 5
    ventana = reloj.ahora() + timedelta(days=31)
    assert Franja.objects.filter(inicio__lte=ventana).count() > 1000  # franjas de 30 días laborables
    assert Franja.objects.filter(cupo_prioritario=True).exists()


def test_la_siembra_crea_afiliados_representante_y_personal(sembrado):
    assert Usuario.objects.filter(rol__in=["AFILIADO", "REPRESENTANTE"]).count() == 51
    assert Usuario.objects.filter(rol="REPRESENTANTE").count() == 1
    for rol in ("RECEPCION", "PROFESIONAL", "SOPORTE", "ADMIN_OFERTA", "AUDITOR_DPD", "ADMIN_TI"):
        assert Usuario.objects.filter(rol=rol, estado="ACTIVA").exists(), rol


def test_todas_las_cedulas_son_sinteticas_pero_con_formato_valido(sembrado):
    for cedula in Usuario.objects.values_list("cedula", flat=True):
        assert es_valida(cedula) and cedula.startswith("175"), cedula  # 175... = rango ficticio del prototipo


def test_los_contactos_son_de_un_dominio_reservado_y_nunca_reales(sembrado):
    for correo in Usuario.objects.values_list("correo", flat=True):
        assert correo.endswith("@correo.iess-prueba.example"), correo


def test_los_afiliados_quedan_pendientes_de_activacion_y_sin_clave_utilizable(sembrado):
    pendientes = Usuario.objects.filter(estado="PENDIENTE_ACTIVACION")
    assert pendientes.count() >= 46
    assert all(not u.has_usable_password() for u in pendientes)


def test_el_contacto_sembrado_coincide_con_el_servicio_de_afiliacion(sembrado):
    fuente = {a["cedula"]: a for a in generar_afiliados()}
    for u in Usuario.objects.filter(cedula__in=fuente):
        assert u.correo == fuente[u.cedula]["correo"] and u.telefono == fuente[u.cedula]["telefono"]


def test_las_claves_de_funcionarios_se_muestran_una_vez_y_cumplen_las_reglas(sembrado):
    lineas = re.findall(r"^\s+(175\d{7})\s+(\S+(?: \(demo\))?)\s+(\S{12,})$", sembrado, re.M)
    assert len(lineas) >= 9  # 7 funcionarios + 3 cuentas demo (2 afiliados y el representante)
    for cedula, rol, clave in lineas:
        if "ya existía" not in clave:
            assert Usuario.objects.get(cedula=cedula).check_password(clave)


def test_la_siembra_es_idempotente_y_no_cambia_claves_existentes(sembrado):
    usuarios, franjas = Usuario.objects.count(), Franja.objects.count()
    hash_antes = {u.cedula: u.password for u in Usuario.objects.all()}
    salida = io.StringIO()
    call_command("sembrar", stdout=salida)
    assert Usuario.objects.count() == usuarios and Franja.objects.count() == franjas
    assert {u.cedula: u.password for u in Usuario.objects.all()} == hash_antes
    assert "ya existía" in salida.getvalue()


def test_la_siembra_no_deja_claves_en_la_auditoria(sembrado):
    from apps.auditoria.modelos import EventoAuditoria

    claves = re.findall(r"^\s+175\d{7}\s+\S+(?: \(demo\))?\s+(\S{12,})$", sembrado, re.M)
    volcado = " ".join(str(e.detalle) for e in EventoAuditoria.objects.all())
    assert claves and not any(c in volcado for c in claves)

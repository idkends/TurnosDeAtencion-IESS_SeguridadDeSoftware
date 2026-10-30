"""T07 / RT-04 / RT-01 / CA-03 / CA-13: concurrencia REAL con hilos y transacciones verdaderas en PostgreSQL.

Determinista en el resultado: pase lo que pase con el orden de los hilos, el conjunto de resultados válidos es uno solo
(una venta, el resto rechazado con un código de negocio conocido y ningún error inesperado).
"""
import threading
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import connection

from apps.cuentas.modelos import Usuario
from apps.integraciones.servicios import DatosAfiliacion, afiliacion_memoria
from apps.turnos import servicios
from apps.turnos.modelos import EstadoFranja, Franja, SolicitudReserva, Turno
from config.cedula import sintetica
from tests.ayudas import franja_futura

pytestmark = pytest.mark.django_db(transaction=True)

INTENTOS = 1000
HILOS = 50
CODIGOS_DE_NEGOCIO = {"FRANJA_NO_DISPONIBLE", "FRANJA_OCUPADA"}


def _crear_afiliados(n: int, desde: int) -> list[Usuario]:
    usuarios = [
        Usuario(cedula=sintetica(desde + i), rol="AFILIADO", estado="ACTIVA", nombres=f"Persona{i}", apellidos="Concurrente", correo=f"c{i}@correo.iess-prueba.example")
        for i in range(n)
    ]
    for u in usuarios:
        u.set_unusable_password()
    Usuario.objects.bulk_create(usuarios)
    for u in usuarios:
        afiliacion_memoria().agregar(DatosAfiliacion(u.cedula, u.nombres, u.apellidos, u.correo, "0990000000", True, None))
    return usuarios


def _lanzar(tareas, hilos=HILOS):
    """Ejecuta las tareas en `hilos` hilos que arrancan a la vez (barrera) y devuelve (resultados, errores)."""
    barrera = threading.Barrier(hilos)
    resultados, errores = [], []
    cerrojo = threading.Lock()

    def trabajador(lote):
        barrera.wait()
        for tarea in lote:
            try:
                valor = tarea()
                with cerrojo:
                    resultados.append(valor)
            except servicios.ReglaRechazada as error:
                with cerrojo:
                    resultados.append(error.codigo)
            except Exception as error:  # noqa: BLE001
                with cerrojo:
                    errores.append(repr(error))
        connection.close()

    lotes = [tareas[i::hilos] for i in range(hilos)]
    with ThreadPoolExecutor(max_workers=hilos) as pool:
        for futuro in [pool.submit(trabajador, lote) for lote in lotes]:
            futuro.result()
    return resultados, errores


def test_T07_mil_intentos_por_la_ultima_franja_una_sola_venta(oferta):
    franja = franja_futura(oferta["est1"], oferta["esp1"])
    afiliados = _crear_afiliados(INTENTOS, 20_000)

    def intento(u):
        return lambda: ("VENTA", servicios.reservar(solicitante=u, franja_id=franja.id, motivo="", clave_idempotencia=uuid.uuid4().hex)[0].id)

    resultados, errores = _lanzar([intento(u) for u in afiliados])

    assert errores == [], errores[:3]  # ninguna excepción inesperada (ni de integridad sin traducir, ni interbloqueos)
    ventas = [r for r in resultados if isinstance(r, tuple)]
    rechazos = Counter(r for r in resultados if isinstance(r, str))
    assert len(ventas) == 1, f"debía haber 1 venta y hubo {len(ventas)}"
    assert sum(rechazos.values()) == INTENTOS - 1 and set(rechazos) <= CODIGOS_DE_NEGOCIO, rechazos
    assert Turno.objects.filter(franja=franja).count() == 1
    assert Franja.objects.get(pk=franja.pk).estado == EstadoFranja.OCUPADA
    assert SolicitudReserva.objects.count() == 1  # los rechazados no dejan clave de idempotencia ni turnos huérfanos
    assert Turno.objects.get(franja=franja).id == ventas[0][1]


def test_RT01_la_misma_persona_en_20_franjas_a_la_vez_obtiene_un_solo_turno_por_especialidad(oferta, parametro):
    parametro("limite_reservas_por_hora_cuenta", 1000)  # aquí se prueba la base, no el límite por cuenta
    (afiliado,) = _crear_afiliados(1, 30_000)
    franjas = [franja_futura(oferta["est1"], oferta["esp1"], dias=2 + (i // 6), hora=8 + i % 6) for i in range(20)]

    def intento(f):
        return lambda: ("VENTA", servicios.reservar(solicitante=afiliado, franja_id=f.id, motivo="", clave_idempotencia=uuid.uuid4().hex)[0].id)

    resultados, errores = _lanzar([intento(f) for f in franjas], hilos=20)
    assert errores == [], errores[:3]
    assert len([r for r in resultados if isinstance(r, tuple)]) == 1
    assert set(r for r in resultados if isinstance(r, str)) <= {"YA_TIENE_CITA_ESPECIALIDAD", "SOLAPE_DE_HORARIO", "TOPE_DE_CITAS"}
    assert Turno.objects.filter(afiliado=afiliado, estado__in=["PENDIENTE", "RESERVADO", "CONFIRMADO"]).count() == 1
    assert Franja.objects.filter(estado=EstadoFranja.OCUPADA).count() == 1  # solo UNA franja quedó ocupada: sin cupos fantasma


def test_CA13_20_solicitudes_simultaneas_con_la_misma_clave_crean_un_solo_turno(oferta, parametro):
    parametro("limite_reservas_por_hora_cuenta", 1000)
    (afiliado,) = _crear_afiliados(1, 40_000)
    franja = franja_futura(oferta["est1"], oferta["esp1"])
    clave = uuid.uuid4().hex

    def intento():
        turno, creado = servicios.reservar(solicitante=afiliado, franja_id=franja.id, motivo="CONTROL", clave_idempotencia=clave)
        return ("OK", turno.id, creado)

    resultados, errores = _lanzar([intento] * 20, hilos=20)
    assert errores == [], errores[:3]
    ids = {r[1] for r in resultados if isinstance(r, tuple)}
    assert len(ids) == 1 and len([r for r in resultados if isinstance(r, tuple) and r[2]]) == 1  # una creó; las demás repitieron
    assert Turno.objects.count() == 1 and SolicitudReserva.objects.count() == 1


def test_cancelar_y_reservar_a_la_vez_no_deja_el_cupo_en_un_estado_inconsistente(oferta):
    """Una persona cancela mientras otras 30 intentan tomar su cupo: el cupo termina con exactamente un dueño activo."""
    dueño, *otros = _crear_afiliados(31, 50_000)
    franja = franja_futura(oferta["est1"], oferta["esp1"])
    turno, _ = servicios.reservar(solicitante=dueño, franja_id=franja.id, motivo="", clave_idempotencia=uuid.uuid4().hex)

    tareas = [lambda: ("CANCELA", servicios.cancelar_turno(dueño, turno.id).estado)] + [
        (lambda u=u: ("VENTA", servicios.reservar(solicitante=u, franja_id=franja.id, motivo="", clave_idempotencia=uuid.uuid4().hex)[0].id)) for u in otros
    ]
    resultados, errores = _lanzar(tareas, hilos=31)
    assert errores == [], errores[:3]
    activos = Turno.objects.filter(franja=franja, estado__in=["PENDIENTE", "RESERVADO", "CONFIRMADO"]).count()
    ocupada = Franja.objects.get(pk=franja.pk).estado == EstadoFranja.OCUPADA
    assert activos <= 1 and ocupada == (activos == 1)  # franja ocupada si y solo si hay un turno activo
    assert Turno.objects.get(pk=turno.id).estado == "CANCELADO_AFILIADO"

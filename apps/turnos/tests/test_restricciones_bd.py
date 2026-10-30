"""Reglas de turnos impuestas por la PROPIA base de datos (RT-01, RT-03, RT-04, idempotencia): aunque falle la aplicación.

Se insertan filas con el ORM directamente a propósito: es justo lo que haría un error de código o una solicitud
manipulada, y la base debe rechazarlo.
"""
from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.turnos.modelos import Especialidad, Establecimiento, Franja, SolicitudReserva, Turno

pytestmark = pytest.mark.django_db


@pytest.fixture
def oferta():
    est = Establecimiento.objects.create(codigo="T01", nombre="Centro de prueba")
    esp = Especialidad.objects.create(codigo="E01", nombre="Medicina general")
    esp2 = Especialidad.objects.create(codigo="E02", nombre="Pediatría")
    base = timezone.now().replace(minute=0, second=0, microsecond=0) + timedelta(days=3)

    def franja(especialidad=esp, desplazamiento_min=0, duracion=30):
        inicio = base + timedelta(minutes=desplazamiento_min)
        return Franja.objects.create(
            establecimiento=est, especialidad=especialidad, inicio=inicio, fin=inicio + timedelta(minutes=duracion)
        )

    return {"est": est, "esp": esp, "esp2": esp2, "franja": franja}


def _turno(afiliado, franja, estado="RESERVADO"):
    return Turno.objects.create(
        afiliado=afiliado, solicitante=afiliado, especialidad=franja.especialidad, franja=franja,
        estado=estado, inicio=franja.inicio, fin=franja.fin,
    )


def test_RT01_segunda_reserva_activa_misma_especialidad_rechazada_por_la_base(crear_usuario, oferta):
    afiliado = crear_usuario()
    _turno(afiliado, oferta["franja"](desplazamiento_min=0))
    with pytest.raises(IntegrityError) as error, transaction.atomic():
        _turno(afiliado, oferta["franja"](desplazamiento_min=300))  # otra hora, misma especialidad
    assert "uq_turno_afiliado_especialidad" in str(error.value)


def test_RT01_un_turno_pendiente_tambien_cuenta_como_activo(crear_usuario, oferta):
    afiliado = crear_usuario()
    _turno(afiliado, oferta["franja"](desplazamiento_min=0), estado="PENDIENTE")
    with pytest.raises(IntegrityError), transaction.atomic():
        _turno(afiliado, oferta["franja"](desplazamiento_min=300), estado="RESERVADO")


def test_RT01_puede_reservar_de_nuevo_si_el_turno_anterior_se_cancelo(crear_usuario, oferta):
    afiliado = crear_usuario()
    _turno(afiliado, oferta["franja"](desplazamiento_min=0), estado="CANCELADO_AFILIADO")
    _turno(afiliado, oferta["franja"](desplazamiento_min=300))  # no debe fallar


def test_RT01_otra_especialidad_si_se_permite(crear_usuario, oferta):
    afiliado = crear_usuario()
    _turno(afiliado, oferta["franja"](oferta["esp"], 0))
    _turno(afiliado, oferta["franja"](oferta["esp2"], 300))


def test_RT04_franja_ocupada_rechazada_por_la_base(crear_usuario, oferta):
    franja = oferta["franja"]()
    _turno(crear_usuario(), franja)
    with pytest.raises(IntegrityError) as error, transaction.atomic():
        _turno(crear_usuario(), franja)  # OTRO afiliado, misma franja
    assert "uq_turno_franja" in str(error.value)


def test_RT04_la_franja_vuelve_a_ser_reservable_tras_cancelar(crear_usuario, oferta):
    franja = oferta["franja"]()
    _turno(crear_usuario(), franja, estado="EXPIRADO")
    _turno(crear_usuario(), franja)


def test_RT03_solape_de_horarios_rechazado_por_la_base(crear_usuario, oferta):
    afiliado = crear_usuario()
    _turno(afiliado, oferta["franja"](oferta["esp"], desplazamiento_min=0, duracion=30))
    with pytest.raises(IntegrityError) as error, transaction.atomic():
        # Otra especialidad (no choca con RT-01) y otra franja, pero a la misma hora (solapa 15 min).
        _turno(afiliado, oferta["franja"](oferta["esp2"], desplazamiento_min=15, duracion=30))
    assert "ex_turno_sin_solape" in str(error.value)


def test_RT03_turnos_contiguos_no_solapan(crear_usuario, oferta):
    afiliado = crear_usuario()
    _turno(afiliado, oferta["franja"](oferta["esp"], desplazamiento_min=0, duracion=30))
    _turno(afiliado, oferta["franja"](oferta["esp2"], desplazamiento_min=30, duracion=30))


def test_RT03_el_solape_entre_afiliados_distintos_es_normal(crear_usuario, oferta):
    _turno(crear_usuario(), oferta["franja"](oferta["esp"], 0))
    _turno(crear_usuario(), oferta["franja"](oferta["esp2"], 0))  # misma hora, otra persona: permitido


def test_idempotencia_unica_por_afiliado_y_clave(crear_usuario):
    afiliado = crear_usuario()
    SolicitudReserva.objects.create(afiliado=afiliado, solicitante=afiliado, clave_idempotencia="clave-0001", huella="a")
    with pytest.raises(IntegrityError) as error, transaction.atomic():
        SolicitudReserva.objects.create(afiliado=afiliado, solicitante=afiliado, clave_idempotencia="clave-0001", huella="b")
    assert "uq_idempotencia" in str(error.value)


def test_idempotencia_misma_clave_en_otro_afiliado_es_valida(crear_usuario):
    a, b = crear_usuario(), crear_usuario()
    SolicitudReserva.objects.create(afiliado=a, solicitante=a, clave_idempotencia="clave-0001", huella="a")
    SolicitudReserva.objects.create(afiliado=b, solicitante=b, clave_idempotencia="clave-0001", huella="a")


def test_idempotencia_rechaza_claves_con_formato_invalido(crear_usuario):
    afiliado = crear_usuario()
    with pytest.raises(IntegrityError), transaction.atomic():
        SolicitudReserva.objects.create(afiliado=afiliado, solicitante=afiliado, clave_idempotencia="corta", huella="a")


def test_CHECK_de_estado_rechaza_valores_fuera_del_ciclo_de_vida(crear_usuario, oferta):
    with pytest.raises(IntegrityError) as error, transaction.atomic():
        _turno(crear_usuario(), oferta["franja"](), estado="INVENTADO")
    assert "ck_turno_estado" in str(error.value)


def test_CHECK_franja_con_fin_anterior_al_inicio_rechazada(oferta):
    inicio = timezone.now() + timedelta(days=2)
    with pytest.raises(IntegrityError), transaction.atomic():
        Franja.objects.create(establecimiento=oferta["est"], especialidad=oferta["esp"], inicio=inicio, fin=inicio - timedelta(minutes=5))


def test_btree_gist_es_la_unica_extension_instalada():
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute("SELECT extname FROM pg_extension ORDER BY 1")
        extensiones = {fila[0] for fila in cursor.fetchall()}
    assert "btree_gist" in extensiones
    assert extensiones <= {"plpgsql", "btree_gist"}, extensiones  # nada de postgis, pgcrypto, pg_trgm...

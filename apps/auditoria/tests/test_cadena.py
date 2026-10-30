"""Auditoría de solo adición (R22, CA-10, T11b): la cadena detecta alteraciones y la base rechaza UPDATE/DELETE/TRUNCATE."""
import threading

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import InternalError, DatabaseError, connection, transaction
from django.test import override_settings

from apps.auditoria import servicios as auditoria
from apps.auditoria.modelos import EventoAuditoria


def _eventos(n=5):
    return [auditoria.registrar(accion="PRUEBA", detalle={"i": i}) for i in range(n)]


@pytest.mark.django_db
def test_los_eventos_se_encadenan_y_la_cadena_verifica():
    eventos = _eventos(5)
    assert eventos[0].hash_anterior == auditoria.HASH_GENESIS
    for anterior, actual in zip(eventos, eventos[1:]):
        assert actual.hash_anterior == anterior.hash
    resultado = auditoria.verificar_cadena()
    assert resultado.correcta and resultado.eventos_revisados == 5


@pytest.mark.django_db
def test_verificar_auditoria_sale_bien_con_una_cadena_integra(capsys):
    _eventos(3)
    call_command("verificar_auditoria")
    assert "íntegra" in capsys.readouterr().out


@pytest.mark.django_db
def test_CA10_la_base_rechaza_update_delete_y_truncate_incluso_al_dueno():
    evento = _eventos(1)[0]
    for sql in (
        "UPDATE auditoria_evento SET accion = 'ALTERADA' WHERE secuencia = %s",
        "DELETE FROM auditoria_evento WHERE secuencia = %s",
    ):
        with pytest.raises(DatabaseError) as error, transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(sql, [evento.secuencia])
        assert "solo adición" in str(error.value)
    with pytest.raises(DatabaseError) as error, transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("TRUNCATE auditoria_evento")
    assert "solo adición" in str(error.value)
    assert EventoAuditoria.objects.filter(secuencia=evento.secuencia, accion="PRUEBA").exists()


@pytest.mark.django_db
def test_el_modelo_tampoco_permite_editar_ni_borrar():
    evento = _eventos(1)[0]
    evento.accion = "OTRA"
    with pytest.raises(RuntimeError):
        evento.save()
    with pytest.raises(RuntimeError):
        evento.delete()


def _alterar_saltando_la_proteccion(sql, params):
    """Simula a un atacante con acceso total a la base: desactiva el disparador y edita. La cadena debe delatarlo."""
    with connection.cursor() as cursor:
        cursor.execute("ALTER TABLE auditoria_evento DISABLE TRIGGER auditoria_sin_update_delete")
        try:
            cursor.execute(sql, params)
        finally:
            cursor.execute("ALTER TABLE auditoria_evento ENABLE TRIGGER auditoria_sin_update_delete")


@pytest.mark.django_db
def test_T11b_una_fila_alterada_a_mano_la_detecta_la_verificacion():
    eventos = _eventos(5)
    _alterar_saltando_la_proteccion("UPDATE auditoria_evento SET resultado = 'DENEGADO' WHERE secuencia = %s", [eventos[2].secuencia])
    resultado = auditoria.verificar_cadena()
    assert not resultado.correcta and resultado.primera_falla == eventos[2].secuencia
    assert resultado.motivo == "contenido alterado"
    with pytest.raises(CommandError, match="CADENA ALTERADA"):
        call_command("verificar_auditoria")


@pytest.mark.django_db
def test_T11b_una_fila_borrada_en_medio_rompe_la_cadena():
    eventos = _eventos(5)
    _alterar_saltando_la_proteccion("DELETE FROM auditoria_evento WHERE secuencia = %s", [eventos[2].secuencia])
    resultado = auditoria.verificar_cadena()
    assert not resultado.correcta and resultado.primera_falla == eventos[3].secuencia
    assert resultado.motivo == "eslabón anterior no coincide"


@pytest.mark.django_db
def test_el_hash_se_firma_con_hmac_y_clave_versionada(settings):
    evento = auditoria.registrar(accion="PRUEBA")
    assert evento.version_clave == settings.AUDITORIA_VERSION_ACTUAL and len(evento.hash) == 64
    # Rotación de clave: los eventos nuevos usan la versión nueva y los viejos siguen verificándose con la suya.
    with override_settings(AUDITORIA_VERSION_ACTUAL=2):
        nuevo = auditoria.registrar(accion="PRUEBA")
    assert nuevo.version_clave == 2
    assert auditoria.verificar_cadena().correcta


@pytest.mark.django_db
def test_una_clave_de_auditoria_distinta_hace_fallar_la_verificacion(settings):
    _eventos(2)
    settings.AUDITORIA_CLAVES = {1: b"otra-clave-que-no-es-la-real", 2: b"x"}
    assert not auditoria.verificar_cadena().correcta  # sin la clave HMAC no se puede reconstruir la cadena


@pytest.mark.django_db
def test_el_detalle_se_redacta_sin_secretos():
    evento = auditoria.registrar(
        accion="PRUEBA",
        detalle={"clave": "Secreta123", "password": "x", "token_recuperacion": "abc", "codigo_activacion": "12345678",
                 "totp_secreto": "JBSW", "Authorization": "Bearer xyz", "motivo": "ok", "anidado": {"cookie": "sid", "n": 1}},
    )
    assert evento.detalle["motivo"] == "ok"
    for clave in ("clave", "password", "token_recuperacion", "codigo_activacion", "totp_secreto", "Authorization"):
        assert evento.detalle[clave] == "[REDACTADO]", clave
    assert evento.detalle["anidado"] == {"cookie": "[REDACTADO]", "n": 1}
    volcado = str(EventoAuditoria.objects.get(pk=evento.pk).detalle)
    assert "Secreta123" not in volcado and "12345678" not in volcado


@pytest.mark.django_db
def test_los_textos_largos_se_recortan():
    evento = auditoria.registrar(accion="PRUEBA", detalle={"motivo": "x" * 5000})
    assert len(evento.detalle["motivo"]) == 500


@pytest.mark.django_db
def test_el_evento_toma_ip_y_correlacion_del_contexto():
    evento = auditoria.registrar(accion="PRUEBA")
    assert evento.ip == "203.0.113.7" and evento.correlacion


@pytest.mark.django_db(transaction=True)
def test_escritores_simultaneos_no_bifurcan_la_cadena():
    """Veinte hilos escribiendo a la vez: el bloqueo consultivo serializa y la cadena queda íntegra."""
    errores = []

    def escribir(n):
        try:
            auditoria.registrar(accion="CONCURRENTE", detalle={"n": n})
        except Exception as error:  # noqa: BLE001
            errores.append(error)
        finally:
            connection.close()

    hilos = [threading.Thread(target=escribir, args=(n,)) for n in range(20)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    assert not errores, errores
    resultado = auditoria.verificar_cadena()
    assert resultado.correcta and resultado.eventos_revisados == 20
    assert len(set(EventoAuditoria.objects.values_list("hash_anterior", flat=True))) == 20  # ningún eslabón repetido


@pytest.mark.django_db
def test_las_referencias_no_reversibles_no_se_redactan_porque_la_deteccion_las_necesita():
    evento = auditoria.registrar(accion="PRUEBA", detalle={"token_ref": "a1b2c3d4e5f6", "cedula_ref": "0123456789abcdef", "token": "secreto-completo"})
    assert evento.detalle["token_ref"] == "a1b2c3d4e5f6" and evento.detalle["cedula_ref"] == "0123456789abcdef"
    assert evento.detalle["token"] == "[REDACTADO]"  # el token entero sí se redacta

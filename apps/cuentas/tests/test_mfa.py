"""Segundo factor TOTP obligatorio para funcionarios y operaciones sensibles; 10 códigos de recuperación (Anexo E)."""
from datetime import timedelta

import pytest
from cryptography.fernet import Fernet
from django.conf import settings

from apps.cuentas import servicios
from apps.cuentas.modelos import CodigoRecuperacionMfa, Usuario
from config import reloj

pytestmark = pytest.mark.django_db


def _enrolar_funcionario(crear_usuario, crear_request, clave_buena, codigo_totp, rol="RECEPCION"):
    """Primer ingreso de un funcionario: clave -> configurar TOTP -> sesión + 10 códigos de recuperación."""
    usuario = crear_usuario(rol)
    request = crear_request()
    assert servicios.autenticar(request, usuario.cedula, clave_buena).estado == "MFA_CONFIGURAR"
    configuracion = servicios.obtener_configuracion_mfa(request)
    resultado = servicios.confirmar_configuracion_mfa(request, codigo_totp(configuracion.secreto))
    return usuario, configuracion.secreto, resultado, request


def _enrolar_ciudadano(crear_usuario, crear_request, clave_buena, codigo_totp):
    """Afiliado que ya inició sesión con su clave y activa voluntariamente el segundo factor."""
    usuario = crear_usuario("AFILIADO")
    request = crear_request()
    assert servicios.autenticar(request, usuario.cedula, clave_buena).estado == "AUTENTICADO"
    configuracion = servicios.obtener_configuracion_mfa(request)
    servicios.confirmar_configuracion_mfa(request, codigo_totp(configuracion.secreto))
    return usuario, configuracion.secreto, request


def test_funcionario_sin_segundo_factor_no_obtiene_sesion_solo_con_la_clave(crear_usuario, crear_request, clave_buena):
    usuario = crear_usuario("SOPORTE")
    request = crear_request()
    resultado = servicios.autenticar(request, usuario.cedula, clave_buena)
    assert resultado.estado == "MFA_CONFIGURAR"
    assert not request.user.is_authenticated  # la clave sola no abre sesión


@pytest.mark.parametrize("rol", ["RECEPCION", "PROFESIONAL", "SOPORTE", "ADMIN_OFERTA", "ADMIN_TI", "AUDITOR_DPD"])
def test_todos_los_funcionarios_exigen_segundo_factor(rol, crear_usuario, crear_request, clave_buena):
    usuario = crear_usuario(rol)
    assert servicios.autenticar(crear_request(), usuario.cedula, clave_buena).estado == "MFA_CONFIGURAR"


def test_afiliado_sin_totp_entra_solo_con_la_clave(crear_usuario, crear_request, clave_buena):
    usuario = crear_usuario("AFILIADO")
    assert servicios.autenticar(crear_request(), usuario.cedula, clave_buena).estado == "AUTENTICADO"


def test_enrolar_entrega_10_codigos_de_recuperacion_y_abre_sesion(crear_usuario, crear_request, clave_buena, codigo_totp):
    usuario, _, resultado, request = _enrolar_funcionario(crear_usuario, crear_request, clave_buena, codigo_totp)
    assert resultado.estado == "AUTENTICADO" and request.user.is_authenticated
    assert len(resultado.codigos_recuperacion) == 10 == len(set(resultado.codigos_recuperacion))
    assert CodigoRecuperacionMfa.objects.filter(usuario=usuario, usado=False).count() == 10


def test_el_secreto_totp_se_guarda_cifrado(crear_usuario, crear_request, clave_buena, codigo_totp):
    usuario, secreto, _, _ = _enrolar_funcionario(crear_usuario, crear_request, clave_buena, codigo_totp)
    guardado = Usuario.objects.get(pk=usuario.pk).totp_secreto_cifrado
    assert secreto not in guardado
    assert Fernet(settings.TOTP_CLAVES_CIFRADO[0].encode()).decrypt(guardado.encode()).decode() == secreto


def test_los_codigos_de_recuperacion_se_guardan_solo_como_resumen(crear_usuario, crear_request, clave_buena, codigo_totp):
    usuario, _, resultado, _ = _enrolar_funcionario(crear_usuario, crear_request, clave_buena, codigo_totp)
    guardados = set(CodigoRecuperacionMfa.objects.values_list("codigo_hash", flat=True))
    for codigo in resultado.codigos_recuperacion:
        assert codigo not in guardados and codigo.replace("-", "") not in guardados


def test_login_posterior_con_totp(crear_usuario, crear_request, clave_buena, codigo_totp):
    usuario, secreto, _, _ = _enrolar_funcionario(crear_usuario, crear_request, clave_buena, codigo_totp)
    reloj.avanzar(timedelta(seconds=90))  # un código nuevo (el del enrolamiento ya se usó)
    request = crear_request()
    assert servicios.autenticar(request, usuario.cedula, clave_buena).estado == "MFA_REQUERIDO"
    assert not request.user.is_authenticated
    assert servicios.verificar_mfa_login(request, codigo_totp(secreto)).estado == "AUTENTICADO"
    assert request.user.is_authenticated


def test_un_codigo_totp_no_se_puede_reutilizar(crear_usuario, crear_request, clave_buena, codigo_totp):
    usuario, secreto, _, _ = _enrolar_funcionario(crear_usuario, crear_request, clave_buena, codigo_totp)
    reloj.avanzar(timedelta(seconds=90))
    codigo = codigo_totp(secreto)
    r1 = crear_request()
    servicios.autenticar(r1, usuario.cedula, clave_buena)
    servicios.verificar_mfa_login(r1, codigo)
    r2 = crear_request()
    servicios.autenticar(r2, usuario.cedula, clave_buena)
    with pytest.raises(servicios.CodigoInvalido):  # el mismo código, otra vez
        servicios.verificar_mfa_login(r2, codigo)


def test_tolerancia_de_un_paso_y_no_mas(crear_usuario, crear_request, clave_buena, codigo_totp):
    usuario, secreto, _, _ = _enrolar_funcionario(crear_usuario, crear_request, clave_buena, codigo_totp)
    reloj.avanzar(timedelta(seconds=300))
    r = crear_request()
    servicios.autenticar(r, usuario.cedula, clave_buena)
    with pytest.raises(servicios.CodigoInvalido):
        servicios.verificar_mfa_login(r, codigo_totp(secreto, desfase_segundos=-120))  # 4 pasos atrás
    assert servicios.verificar_mfa_login(r, codigo_totp(secreto, desfase_segundos=-30)).estado == "AUTENTICADO"  # 1 paso atrás


def test_codigo_totp_incorrecto_rechazado_y_cuenta_para_el_bloqueo(crear_usuario, crear_request, clave_buena, codigo_totp, parametro):
    parametro("totp_intentos_por_minuto_ip", 1000)
    usuario, secreto, _, _ = _enrolar_funcionario(crear_usuario, crear_request, clave_buena, codigo_totp)
    reloj.avanzar(timedelta(seconds=90))
    r = crear_request()
    servicios.autenticar(r, usuario.cedula, clave_buena)
    for _ in range(5):
        with pytest.raises(servicios.CodigoInvalido):
            servicios.verificar_mfa_login(r, "000000")
    assert servicios._bloqueada(usuario.cedula)  # 5 fallos seguidos: la cuenta queda bloqueada también por el 2.º factor
    with pytest.raises(servicios.CredencialesInvalidas):
        servicios.verificar_mfa_login(r, codigo_totp(secreto))


def test_codigo_de_recuperacion_de_un_solo_uso(crear_usuario, crear_request, clave_buena, codigo_totp):
    usuario, _, resultado, _ = _enrolar_funcionario(crear_usuario, crear_request, clave_buena, codigo_totp)
    codigo = resultado.codigos_recuperacion[0]
    r1 = crear_request()
    servicios.autenticar(r1, usuario.cedula, clave_buena)
    assert servicios.verificar_mfa_login(r1, codigo).estado == "AUTENTICADO"
    r2 = crear_request()
    servicios.autenticar(r2, usuario.cedula, clave_buena)
    with pytest.raises(servicios.CodigoInvalido):
        servicios.verificar_mfa_login(r2, codigo)  # ya se usó
    assert CodigoRecuperacionMfa.objects.filter(usuario=usuario, usado=True).count() == 1


def test_quien_tiene_la_clave_no_puede_reemplazar_el_segundo_factor(crear_usuario, crear_request, clave_buena, codigo_totp):
    usuario, _, _, _ = _enrolar_funcionario(crear_usuario, crear_request, clave_buena, codigo_totp)
    atacante = crear_request()
    servicios.autenticar(atacante, usuario.cedula, clave_buena)  # solo conoce la clave
    with pytest.raises(servicios.AccesoDenegado):
        servicios.obtener_configuracion_mfa(atacante)


def test_la_configuracion_pendiente_se_reutiliza_si_hay_un_error_de_tipeo(crear_usuario, crear_request, clave_buena):
    usuario = crear_usuario("SOPORTE")
    request = crear_request()
    servicios.autenticar(request, usuario.cedula, clave_buena)
    primera = servicios.obtener_configuracion_mfa(request)
    with pytest.raises(servicios.CodigoInvalido):
        servicios.confirmar_configuracion_mfa(request, "000000")
    assert servicios.obtener_configuracion_mfa(request).secreto == primera.secreto


# --- Operaciones sensibles ----------------------------------------------------------------------------------------------------------


def test_operacion_sensible_sin_totp_pide_configurarlo(crear_usuario, crear_request, clave_buena):
    usuario = crear_usuario("AFILIADO")
    request = crear_request()
    servicios.autenticar(request, usuario.cedula, clave_buena)
    with pytest.raises(servicios.ReautenticacionRequerida) as error:
        servicios.exigir_operacion_sensible(request)
    assert error.value.configurar_mfa


def test_operacion_sensible_exige_segundo_factor_reciente(crear_usuario, crear_request, clave_buena, codigo_totp):
    usuario, secreto, request = _enrolar_ciudadano(crear_usuario, crear_request, clave_buena, codigo_totp)
    servicios.exigir_operacion_sensible(request)  # recién verificado: pasa
    request.session["ultima_actividad"] = reloj.ahora().timestamp()
    reloj.avanzar(timedelta(minutes=11))  # el 2.º factor ya no es reciente (10 min)
    request.session["ultima_actividad"] = reloj.ahora().timestamp()
    with pytest.raises(servicios.ReautenticacionRequerida) as error:
        servicios.exigir_operacion_sensible(request)
    assert error.value.motivo == "MFA_NO_RECIENTE"
    servicios.reautenticar(request, clave_buena, codigo_totp(secreto))
    servicios.exigir_operacion_sensible(request)  # tras reautenticar vuelve a pasar


def test_T03_sesion_con_mas_de_1_hora_de_inactividad_no_hace_operaciones_sensibles(crear_usuario, crear_request, clave_buena, codigo_totp):
    usuario, secreto, request = _enrolar_ciudadano(crear_usuario, crear_request, clave_buena, codigo_totp)
    request.session["mfa_en"] = reloj.ahora().timestamp() + 7200  # aísla la regla de inactividad
    request.actividad_previa = reloj.ahora().timestamp() - 61 * 60  # última actividad hace 61 minutos
    with pytest.raises(servicios.ReautenticacionRequerida) as error:
        servicios.exigir_operacion_sensible(request)
    assert error.value.motivo == "INACTIVIDAD"
    request.actividad_previa = reloj.ahora().timestamp() - 59 * 60
    servicios.exigir_operacion_sensible(request)  # 59 minutos: todavía sirve

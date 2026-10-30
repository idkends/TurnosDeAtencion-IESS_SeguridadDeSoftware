import pyotp
import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory

from config import reloj


@pytest.fixture
def crear_request(db):
    """Solicitud con sesión real (guardada en la base) para ejercitar los servicios sin pasar por las vistas."""

    def _crear(cookies=None, agente="Mozilla/5.0 (Windows NT 10.0) Firefox/130.0"):
        request = RequestFactory().post("/", HTTP_USER_AGENT=agente)
        SessionMiddleware(lambda r: None).process_request(request)
        request.session.save()
        request.user = AnonymousUser()
        request.COOKIES = dict(cookies or {})
        return request

    return _crear


@pytest.fixture
def codigo_totp():
    """Código TOTP vigente según el reloj de la aplicación (no el del sistema)."""

    def _codigo(secreto, desfase_segundos=0):
        return pyotp.TOTP(secreto).at(int(reloj.ahora().timestamp()) + desfase_segundos)

    return _codigo

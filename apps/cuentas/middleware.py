"""Middleware de sesión: vence la sesión a las 24 h y anota la actividad. Toda la lógica vive en `servicios`."""
from . import servicios


class SesionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            servicios.registrar_actividad(request)
        return self.get_response(request)

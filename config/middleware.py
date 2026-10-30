"""Middleware transversal: contexto de la solicitud (IP, dispositivo, correlación) y cabeceras extra."""
import hashlib
import uuid

from django.conf import settings

from config import contexto


def ip_cliente(request) -> str | None:
    """IP del cliente según el proxy de confianza (sobrescribe X-Forwarded-For con un solo valor)."""
    valor = request.META.get(settings.IP_CLIENTE_CABECERA) or request.META.get("REMOTE_ADDR")
    if not valor:
        return None
    return valor.split(",")[-1].strip() or None


def resumen_dispositivo(valor_cookie: str | None) -> str:
    """El identificador de dispositivo (cookie) solo se usa como resumen; nunca se registra en claro."""
    if not valor_cookie:
        return ""
    return hashlib.sha256(valor_cookie.encode()).hexdigest()


class ContextoSolicitudMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        correlacion = request.headers.get("X-Request-ID", "")
        if not (correlacion and len(correlacion) <= 64 and correlacion.replace("-", "").isalnum()):
            correlacion = uuid.uuid4().hex
        token = contexto.fijar(
            ip=ip_cliente(request),
            dispositivo=resumen_dispositivo(request.COOKIES.get(settings.COOKIE_DISPOSITIVO)),
            correlacion=correlacion,
        )
        try:
            respuesta = self.get_response(request)
        finally:
            contexto.restablecer(token)
        respuesta["X-Request-ID"] = correlacion
        return respuesta


class CabecerasSeguridadMiddleware:
    """Cabeceras que Django no agrega por sí solo y respuestas sin caché (sesiones y tokens no se guardan)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        respuesta = self.get_response(request)
        respuesta.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()")
        respuesta.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        if not request.path.startswith(settings.STATIC_URL):
            respuesta.setdefault("Cache-Control", "no-store")
        return respuesta

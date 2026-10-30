"""Middleware transversal: contexto de la solicitud (IP, dispositivo, correlación) y cabeceras extra."""
import hashlib
import time
import uuid

from django.conf import settings

from django.http import HttpResponse

from config import bloqueos, contexto, metricas


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


class BloqueoOrigenMiddleware:
    """Respuesta automática D01/D04: un origen bloqueado temporalmente recibe 403 genérico (salvo /salud, para los healthchecks)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        faltan = bloqueos.origen_bloqueado(contexto.actual().ip) if request.path != "/salud" else 0
        if faltan:
            respuesta = HttpResponse("Acceso temporalmente restringido. Intente más tarde.", status=403, content_type="text/plain; charset=utf-8")
            respuesta["Retry-After"] = str(faltan)
            return respuesta
        return self.get_response(request)


class MetricasMiddleware:
    """Cuenta solicitudes, 5xx y latencia (sin datos personales) para las reglas D12 y D13 y la sala de espera."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        inicio = time.monotonic()
        estado = 500
        try:
            respuesta = self.get_response(request)
            estado = respuesta.status_code
            return respuesta
        finally:
            if request.path != "/salud":
                try:
                    metricas.registrar(time.monotonic() - inicio, estado)
                except Exception:  # noqa: BLE001  (las métricas nunca deben tumbar una solicitud)
                    pass

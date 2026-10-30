"""Navegador mínimo (solo biblioteca estándar) para las demostraciones y el análisis dinámico: mantiene cookies, extrae el token CSRF
de los formularios y NO sigue redirecciones, para poder ver el código exacto que devuelve el servidor."""
import http.cookiejar
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request


class _SinRedireccion(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Respuesta:
    def __init__(self, estado: int, cabeceras, cuerpo: str):
        self.estado, self.cabeceras, self.cuerpo = estado, cabeceras, cuerpo

    @property
    def ubicacion(self) -> str:
        return self.cabeceras.get("Location", "")


class Navegador:
    def __init__(self, base: str, host: str | None = None, ip: str | None = None):
        self.base, self.host, self.ip = base.rstrip("/"), host, ip
        contexto = ssl.create_default_context()
        contexto.check_hostname = False
        contexto.verify_mode = ssl.CERT_NONE  # certificado de desarrollo
        self.cookies = http.cookiejar.CookieJar()
        self._abridor = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookies), urllib.request.HTTPSHandler(context=contexto), _SinRedireccion
        )

    def _pedir(self, ruta: str, datos: dict | None = None, extra: dict | None = None, metodo: str | None = None) -> Respuesta:
        cabeceras = dict(extra or {})
        if self.host:
            cabeceras["Host"] = self.host
        if self.ip:
            cabeceras["X-Forwarded-For"] = self.ip
        cuerpo = urllib.parse.urlencode(datos).encode() if datos is not None else None
        peticion = urllib.request.Request(self.base + ruta, data=cuerpo, headers=cabeceras, method=metodo)
        try:
            r = self._abridor.open(peticion, timeout=20)
        except urllib.error.HTTPError as error:
            r = error
        return Respuesta(r.status if hasattr(r, "status") else r.code, r.headers, r.read().decode("utf-8", "replace"))

    def get(self, ruta: str, extra: dict | None = None) -> Respuesta:
        return self._pedir(ruta, extra=extra)

    def token_csrf(self) -> str:
        for c in self.cookies:
            if c.name.endswith("csrftoken"):
                return c.value
        for _ in range(12):  # si el límite de solicitudes responde 429, se espera como lo haría una persona (Retry-After)
            r = self.get("/sesion")
            if r.estado == 200:
                return next(c.value for c in self.cookies if c.name.endswith("csrftoken"))
            time.sleep(min(int(r.cabeceras.get("Retry-After", "5") or 5), 10))
        raise RuntimeError("No se pudo obtener el token CSRF (límites de solicitudes)")

    def post(self, ruta: str, datos: dict, extra: dict | None = None) -> Respuesta:
        datos = {**datos, "csrfmiddlewaretoken": self.token_csrf()}
        return self._pedir(ruta, datos, {"Referer": self.base + ruta, **(extra or {})})

    def iniciar_sesion(self, cedula: str, clave: str) -> Respuesta:
        return self.post("/sesion", {"cedula": cedula, "clave": clave})

    def valor_cookie(self, sufijo: str) -> str | None:
        return next((c.value for c in self.cookies if c.name.endswith(sufijo)), None)


def campo(html: str, nombre: str) -> str | None:
    m = re.search(rf'name="{re.escape(nombre)}"[^>]*value="([^"]*)"', html)
    return m.group(1) if m else None

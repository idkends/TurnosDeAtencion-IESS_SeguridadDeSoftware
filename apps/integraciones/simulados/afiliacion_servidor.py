"""Servicio de afiliación SIMULADO (tercero ficticio). Corre en la red `externos` de Docker Compose.

- GET  /afiliados/<cedula>  (requiere token de servicio firmado)
- POST /control             (requiere token) con modo: normal, caido, lento, error5xx o esquema_invalido

Permite fallar a propósito para probar el comportamiento del portal (estado PENDIENTE, circuito abierto).
"""
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from apps.integraciones import servicio_token
from apps.integraciones.sinteticos import generar_afiliados

SECRETO = os.environ.get("SERVICIOS_CLAVE_AFILIACION", "").encode()
REGISTRO = {a["cedula"]: a for a in generar_afiliados()}
MODOS = {"normal", "caido", "lento", "error5xx", "esquema_invalido"}
_estado = {"modo": os.environ.get("AFILIACION_SIM_MODO", "normal"), "latencia_ms": 0}
_cerrojo = threading.Lock()
_CAMPOS = ("cedula", "nombres", "apellidos", "correo", "telefono", "vigente", "fecha_nacimiento")


class Manejador(BaseHTTPRequestHandler):
    server_version = "AfiliacionSim"
    sys_version = ""

    def log_message(self, formato, *args):  # sin cédulas en los registros
        pass

    def _responder(self, codigo: int, cuerpo: dict | None = None):
        crudo = json.dumps(cuerpo or {}).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(crudo)))
        self.end_headers()
        self.wfile.write(crudo)

    def _autorizado(self) -> bool:
        return servicio_token.verificar(SECRETO, self.headers.get("X-Servicio-Token"), self.path)

    def do_GET(self):
        if not self._autorizado():
            return self._responder(401, {"error": "no autorizado"})
        if not self.path.startswith("/afiliados/"):
            return self._responder(404)
        with _cerrojo:
            modo, latencia = _estado["modo"], _estado["latencia_ms"]
        if latencia:
            time.sleep(latencia / 1000)
        if modo == "lento":
            time.sleep(10)
        if modo in {"caido", "error5xx"}:
            return self._responder(503, {"error": "servicio no disponible"})
        cedula = self.path.rsplit("/", 1)[-1]
        afiliado = REGISTRO.get(cedula)
        if afiliado is None:
            return self._responder(404)
        if modo == "esquema_invalido":
            return self._responder(200, {"cedula": cedula, "vigente": "si"})
        return self._responder(200, {c: afiliado[c] for c in _CAMPOS})

    def do_POST(self):
        if self.path != "/control" or not self._autorizado():
            return self._responder(401, {"error": "no autorizado"})
        largo = min(int(self.headers.get("Content-Length", "0") or 0), 1024)
        try:
            datos = json.loads(self.rfile.read(largo) or b"{}")
        except json.JSONDecodeError:
            return self._responder(400)
        with _cerrojo:
            if datos.get("modo") in MODOS:
                _estado["modo"] = datos["modo"]
            if isinstance(datos.get("latencia_ms"), int) and 0 <= datos["latencia_ms"] <= 10000:
                _estado["latencia_ms"] = datos["latencia_ms"]
            return self._responder(200, dict(_estado))


def main():
    if not SECRETO:
        raise SystemExit("Falta SERVICIOS_CLAVE_AFILIACION")
    puerto = int(os.environ.get("AFILIACION_SIM_PUERTO", "9001"))
    ThreadingHTTPServer(("0.0.0.0", puerto), Manejador).serve_forever()  # nosec B104


if __name__ == "__main__":
    main()

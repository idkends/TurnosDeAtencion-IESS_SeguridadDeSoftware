"""Sistema de agenda SIMULADO (tercero ficticio). Corre en la red `externos` de Docker Compose.

- POST   /reservas        {turno_ref, franja_ref, afiliado_ref, clave}  (idempotente por `clave`)
- DELETE /reservas/<ref>
- POST   /control         {modo: normal|caido|lento|rechaza, latencia_ms}
Todo exige token de servicio firmado. Falla a propósito cuando se le pide, para probar el estado PENDIENTE.
"""
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from apps.integraciones import servicio_token

SECRETO = os.environ.get("SERVICIOS_CLAVE_AGENDA", "").encode()
MODOS = {"normal", "caido", "lento", "rechaza"}
_estado = {"modo": os.environ.get("AGENDA_SIM_MODO", "normal"), "latencia_ms": 0}
_confirmaciones: dict[str, dict] = {}
_cerrojo = threading.Lock()
_REF = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class Manejador(BaseHTTPRequestHandler):
    server_version = "AgendaSim"
    sys_version = ""

    def log_message(self, formato, *args):
        pass

    def _responder(self, codigo: int, cuerpo: dict | None = None):
        crudo = json.dumps(cuerpo or {}).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(crudo)))
        self.end_headers()
        self.wfile.write(crudo)

    def _leer(self) -> dict | None:
        largo = min(int(self.headers.get("Content-Length", "0") or 0), 4096)
        try:
            datos = json.loads(self.rfile.read(largo) or b"{}")
        except json.JSONDecodeError:
            return None
        return datos if isinstance(datos, dict) else None

    def _preparar(self) -> bool:
        if not servicio_token.verificar(SECRETO, self.headers.get("X-Servicio-Token"), self.path):
            self._responder(401, {"error": "no autorizado"})
            return False
        with _cerrojo:
            modo, latencia = _estado["modo"], _estado["latencia_ms"]
        if latencia:
            time.sleep(latencia / 1000)
        if modo == "lento":
            time.sleep(10)
        if modo == "caido" and self.path != "/control":
            self._responder(503, {"error": "servicio no disponible"})
            return False
        return True

    def do_POST(self):
        if not self._preparar():
            return
        datos = self._leer()
        if datos is None:
            return self._responder(400)
        if self.path == "/control":
            with _cerrojo:
                if datos.get("modo") in MODOS:
                    _estado["modo"] = datos["modo"]
                if isinstance(datos.get("latencia_ms"), int) and 0 <= datos["latencia_ms"] <= 10000:
                    _estado["latencia_ms"] = datos["latencia_ms"]
                return self._responder(200, dict(_estado))
        if self.path != "/reservas":
            return self._responder(404)
        campos = ("turno_ref", "franja_ref", "afiliado_ref", "clave")
        if set(datos) != set(campos) or not all(isinstance(datos[c], str) and _REF.match(datos[c]) for c in campos):
            return self._responder(422, {"error": "esquema inválido"})
        with _cerrojo:
            previa = _confirmaciones.get(datos["clave"])
            if previa is None:
                estado = "RECHAZADA" if _estado["modo"] == "rechaza" else "CONFIRMADA"
                previa = {"estado": estado, "referencia": f"AG-{len(_confirmaciones) + 1:06d}", "turno_ref": datos["turno_ref"]}
                _confirmaciones[datos["clave"]] = previa
        return self._responder(200, {"estado": previa["estado"], "referencia": previa["referencia"]})

    def do_DELETE(self):
        if not self._preparar():
            return
        referencia = self.path.rsplit("/", 1)[-1]
        with _cerrojo:
            for clave, c in list(_confirmaciones.items()):
                if c["turno_ref"] == referencia:
                    del _confirmaciones[clave]
        return self._responder(200, {})


def main():
    if not SECRETO:
        raise SystemExit("Falta SERVICIOS_CLAVE_AGENDA")
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("AGENDA_SIM_PUERTO", "9002"))), Manejador).serve_forever()


if __name__ == "__main__":
    main()

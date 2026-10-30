"""Inicia sesión con la primera cuenta del JSON de `usuarios_demo` y escribe el valor de la cookie de sesión (para ZAP)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cliente import Navegador  # noqa: E402

cuentas = json.loads(sys.argv[1])
cedula, clave = next(iter(cuentas.items()))
navegador = Navegador(sys.argv[2] if len(sys.argv) > 2 else "https://localhost:8443")
respuesta = navegador.iniciar_sesion(cedula, clave)
if respuesta.estado != 302:
    sys.exit(f"no se pudo iniciar sesión ({respuesta.estado})")
print(navegador.valor_cookie("sessionid"))

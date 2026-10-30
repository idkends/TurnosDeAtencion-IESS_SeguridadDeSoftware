"""Comprueba contra la aplicación EN EJECUCIÓN las cabeceras, cookies y TLS de la lista de la sección 5.5 (T18, R16).

Solo biblioteca estándar: se ejecuta en el anfitrión. Uso: python scripts/verificar_cabeceras.py [https://localhost:8443]
Sale con código 1 si algo falla y escribe el resultado JSON en docs/evidencia/cabeceras_<fecha>.json.
"""
import http.client
import json
import re
import warnings
import socket
import ssl
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

warnings.simplefilter("ignore", DeprecationWarning)  # TLS 1.0 y 1.1 se prueban a propósito
BASE = sys.argv[1] if len(sys.argv) > 1 else "https://localhost:8443"
URL = urlparse(BASE)
resultados: list[dict] = []


def comprobar(nombre: str, ok: bool, detalle: str = "") -> None:
    resultados.append({"comprobacion": nombre, "ok": bool(ok), "detalle": detalle})
    print(("  OK   " if ok else "  FALLA"), nombre, f"({detalle})" if detalle and not ok else "")


def contexto(minima=None, maxima=None) -> ssl.SSLContext:
    c = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    c.check_hostname = False
    c.verify_mode = ssl.CERT_NONE  # certificado de desarrollo (mkcert/autofirmado)
    if minima:
        c.minimum_version = minima
    if maxima:
        c.maximum_version = maxima
    return c


def pedir(ruta: str, metodo: str = "GET", cuerpo: str | None = None, cabeceras: dict | None = None):
    conexion = http.client.HTTPSConnection(URL.hostname, URL.port or 443, context=contexto(), timeout=10)
    conexion.request(metodo, ruta, body=cuerpo, headers=cabeceras or {})
    r = conexion.getresponse()
    datos = r.read().decode("utf-8", "replace")
    return r, datos


def main() -> int:
    print(f"Verificando {BASE}")
    r, html = pedir("/sesion")
    h = {k.lower(): v for k, v in r.getheaders()}
    print("Cabeceras:")
    comprobar("HSTS con includeSubDomains", "max-age=" in h.get("strict-transport-security", "") and "includesubdomains" in h.get("strict-transport-security", "").lower())
    csp = h.get("content-security-policy", "")
    comprobar("CSP default-src 'none'", "default-src 'none'" in csp, csp[:80])
    comprobar("CSP sin 'unsafe-inline' ni 'unsafe-eval'", "unsafe-inline" not in csp and "unsafe-eval" not in csp)
    comprobar("CSP frame-ancestors 'none'", "frame-ancestors 'none'" in csp)
    comprobar("CSP con nonce por solicitud", re.search(r"'nonce-[A-Za-z0-9+/=]+'", csp) is not None)
    nonces = {re.search(r"nonce-([A-Za-z0-9+/=]+)", pedir("/sesion")[0].getheader("Content-Security-Policy") or "").group(1) for _ in range(2)}
    comprobar("El nonce cambia en cada respuesta", len(nonces) == 2)
    comprobar("X-Content-Type-Options: nosniff", h.get("x-content-type-options") == "nosniff")
    comprobar("X-Frame-Options: DENY", h.get("x-frame-options", "").upper() == "DENY")
    comprobar("Referrer-Policy restrictiva", h.get("referrer-policy") in {"same-origin", "no-referrer", "strict-origin", "strict-origin-when-cross-origin"})
    comprobar("Permissions-Policy presente", "camera=()" in h.get("permissions-policy", ""))
    comprobar("Cross-Origin-Opener-Policy", h.get("cross-origin-opener-policy") == "same-origin")
    comprobar("Cross-Origin-Resource-Policy", h.get("cross-origin-resource-policy") == "same-origin")
    comprobar("Respuestas con datos sin caché", "no-store" in h.get("cache-control", ""))
    comprobar("No revela la versión del servidor", not re.search(r"\d", h.get("server", "")) and "x-powered-by" not in h)

    print("Cookies:")
    cookies = [v for k, v in r.getheaders() if k.lower() == "set-cookie"]
    comprobar("Hay cookie CSRF", bool(cookies))
    for c in cookies:
        nombre = c.split("=")[0]
        comprobar(f"{nombre}: Secure, HttpOnly, SameSite, prefijo __Host-, Path=/ y sin Domain",
                  all(x in c for x in ("Secure", "HttpOnly", "SameSite=")) and nombre.startswith("__Host-") and "Path=/" in c and "Domain=" not in c, c)

    print("Errores y métodos:")
    r404, cuerpo404 = pedir("/no-existe-xyz")
    comprobar("404 sin traza ni detalles técnicos", r404.status == 404 and "Traceback" not in cuerpo404 and "Django" not in cuerpo404)
    rt, _ = pedir("/sesion", "TRACE")
    comprobar("TRACE no permitido", rt.status in (400, 403, 404, 405, 501))
    rp, _ = pedir("/sesion", "POST", "cedula=1&clave=x", {"Content-Type": "application/x-www-form-urlencoded"})
    comprobar("POST sin token CSRF rechazado (403)", rp.status == 403)
    rh, _ = pedir("/sesion", cabeceras={"Host": "atacante.example"})
    comprobar("Host no permitido rechazado", rh.status in (400, 421, 444))

    print("TLS:")
    puerto = URL.port or 443
    for nombre, version in (("TLS 1.0", ssl.TLSVersion.TLSv1), ("TLS 1.1", ssl.TLSVersion.TLSv1_1)):
        try:
            with socket.create_connection((URL.hostname, puerto), timeout=5) as s:
                ctx = contexto(minima=version, maxima=version)
                ctx.set_ciphers("ALL:@SECLEVEL=0")
                with ctx.wrap_socket(s, server_hostname=URL.hostname):
                    comprobar(f"{nombre} rechazado", False, "el servidor aceptó")
        except (ssl.SSLError, OSError, ValueError):
            comprobar(f"{nombre} rechazado", True)
    with socket.create_connection((URL.hostname, puerto), timeout=5) as s, contexto(minima=ssl.TLSVersion.TLSv1_2).wrap_socket(s, server_hostname=URL.hostname) as t:
        comprobar("Se negocia TLS 1.2 o superior", t.version() in ("TLSv1.2", "TLSv1.3"), str(t.version()))
    redir = http.client.HTTPConnection(URL.hostname, 18080, timeout=5)
    redir.request("GET", "/sesion")
    rr = redir.getresponse()
    comprobar("HTTP redirige a HTTPS", rr.status in (301, 308) and (rr.getheader("Location") or "").startswith("https://"), str(rr.status))

    fallas = [x for x in resultados if not x["ok"]]
    salida = Path(__file__).resolve().parent.parent / "docs" / "evidencia"
    salida.mkdir(parents=True, exist_ok=True)
    ahora = datetime.now(timezone.utc)
    (salida / f"cabeceras_{ahora:%Y%m%dT%H%M%SZ}.json").write_text(
        json.dumps({"fecha_utc": ahora.isoformat(timespec="seconds"), "url": BASE, "total": len(resultados), "fallas": len(fallas), "resultados": resultados}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\n{len(resultados) - len(fallas)}/{len(resultados)} comprobaciones correctas.")
    return 1 if fallas else 0


if __name__ == "__main__":
    sys.exit(main())

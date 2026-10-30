"""Cabeceras de seguridad de los archivos estáticos (los sirve WhiteNoise antes de pasar por el middleware de la aplicación)."""


def cabeceras(headers, path, url):
    headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
    headers["Cross-Origin-Resource-Policy"] = "same-origin"
    headers["Cross-Origin-Embedder-Policy"] = "require-corp"

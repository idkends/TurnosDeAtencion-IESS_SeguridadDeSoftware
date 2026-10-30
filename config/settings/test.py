"""Pruebas automáticas. Secretos aleatorios por ejecución (nunca fijos en el repositorio)."""
import os
import secrets

for _nombre in ("DJANGO_SECRET_KEY", "HMAC_TOKENS_CLAVE"):
    os.environ.setdefault(_nombre, secrets.token_urlsafe(64))
os.environ.setdefault("PARAM_TIEMPO_MINIMO_RESPUESTA_AUTH_MS", "120")  # pruebas rápidas; en producción 350 ms
os.environ.setdefault("ALLOWED_HOSTS", "testserver,localhost")
os.environ.setdefault("URL_BASE", "https://portal.prueba.local")
os.environ.setdefault("AUDITORIA_CLAVES", "1=" + secrets.token_hex(32) + ",2=" + secrets.token_hex(32))
os.environ.setdefault("AUDITORIA_VERSION_ACTUAL", "1")
os.environ.setdefault("DB_CLAVE", os.environ.get("DB_CLAVE_PRUEBAS", ""))

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("TOTP_CLAVES_CIFRADO", Fernet.generate_key().decode())

from .base import *  # noqa: E402,F401,F403

DATABASES["default"]["USER"] = env("DB_USUARIO_PRUEBAS", "iess_pruebas")  # noqa: F405 (puede crear la base de pruebas)
DATABASES["default"]["PASSWORD"] = env("DB_CLAVE_PRUEBAS")  # noqa: F405
SECURE_SSL_REDIRECT = False
CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "pruebas-default"},
    "limites": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "pruebas-limites"},
}
# Argon2id con costos mínimos para que la batería de pruebas sea rápida (el algoritmo sigue siendo Argon2id).
PASSWORD_HASHERS = ["config.hashers_prueba.Argon2IdRapido"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
NOTIFICACIONES_ADAPTADOR = "memoria"
AFILIACION_ADAPTADOR = "memoria"
AGENDA_ADAPTADOR = "memoria"

MIDDLEWARE = [m for m in MIDDLEWARE if "whitenoise" not in m]  # sin directorio de estáticos compilados en pruebas

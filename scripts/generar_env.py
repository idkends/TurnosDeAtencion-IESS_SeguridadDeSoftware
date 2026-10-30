"""Genera `.env` con secretos aleatorios para el entorno local. No sobrescribe un `.env` existente.

Solo usa la biblioteca estándar y un generador Fernet equivalente (32 bytes en base64 urlsafe), para poder
ejecutarse en el equipo anfitrión sin instalar nada.
"""
import base64
import secrets
import sys
from pathlib import Path

DESTINO = Path(__file__).resolve().parent.parent / ".env"


def clave_fernet() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()


def main() -> int:
    if DESTINO.exists():
        print(f"{DESTINO.name} ya existe; no se modifica.")
        return 0
    valores = {
        "DJANGO_SECRET_KEY": secrets.token_urlsafe(64),
        "DJANGO_SECRET_KEY_FALLBACKS": "",
        "ALLOWED_HOSTS": "localhost,127.0.0.1",
        "URL_BASE": "https://localhost:8443",
        "HSTS_SEGUNDOS": "60",  # corto en local: HSTS sobre "localhost" afecta a todos los puertos del navegador
        "HMAC_TOKENS_CLAVE": secrets.token_urlsafe(48),
        "TOTP_CLAVES_CIFRADO": clave_fernet(),
        "AUDITORIA_CLAVES": "1=" + secrets.token_hex(32),
        "AUDITORIA_VERSION_ACTUAL": "1",
        "POSTGRES_PASSWORD": secrets.token_urlsafe(32),
        "DB_NAME": "iess",
        "DB_CLAVE": secrets.token_urlsafe(32),
        "DB_MIGRACIONES_CLAVE": secrets.token_urlsafe(32),
        "DB_AUDITORIA_RO_CLAVE": secrets.token_urlsafe(32),
        "DB_CLAVE_PRUEBAS": secrets.token_urlsafe(32),
        "RESPALDO_CLAVE": secrets.token_urlsafe(48),
        "REDIS_CLAVE": secrets.token_urlsafe(32),
        "SERVICIOS_CLAVE_AFILIACION": secrets.token_urlsafe(48),
        "SERVICIOS_CLAVE_AGENDA": secrets.token_urlsafe(48),
        "NOTIFICACIONES_ADAPTADOR": "consola",
    }
    DESTINO.write_text("".join(f"{k}={v}\n" for k, v in valores.items()), encoding="utf-8")
    print(f"Creado {DESTINO.name} con secretos aleatorios (no lo subas al repositorio).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

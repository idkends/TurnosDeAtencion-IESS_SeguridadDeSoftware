"""Configuración base. Segura por defecto: los entornos (dev, test) solo relajan lo estrictamente necesario.

Ningún secreto vive aquí: todo sale de variables de entorno (ver `.env.example`).
"""
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from config.parametros import P

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def env(nombre: str, defecto: str | None = None, requerido: bool = True) -> str:
    valor = os.environ.get(nombre, defecto)
    if requerido and (valor is None or valor == ""):
        raise ImproperlyConfigured(f"Falta la variable de entorno {nombre} (ver .env.example).")
    return valor or ""


def env_lista(nombre: str, defecto: str = "", requerido: bool = False) -> list[str]:
    return [x.strip() for x in env(nombre, defecto, requerido).split(",") if x.strip()]


# --- Núcleo ---------------------------------------------------------------------------------------------
DEBUG = False
SECRET_KEY = env("DJANGO_SECRET_KEY")
# Rotación sin invalidar todas las sesiones de golpe (Anexo J): las claves anteriores van aquí.
SECRET_KEY_FALLBACKS = env_lista("DJANGO_SECRET_KEY_FALLBACKS")
ALLOWED_HOSTS = env_lista("ALLOWED_HOSTS", requerido=True)
# Dominio fijo con el que se arman los enlaces (nunca se usa la cabecera Host: CVE-2026-48710, CA-16).
URL_BASE = env("URL_BASE").rstrip("/")
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",  # solo para ExclusionConstraint/BtreeGistExtension; sin gis
    "csp",
    "apps.auditoria",
    "apps.notificaciones",
    "apps.integraciones",
    "apps.cuentas",
    "apps.delegaciones",
    "apps.turnos",
    "apps.deteccion",
    "apps.derechos",
]

AUTH_USER_MODEL = "cuentas.Usuario"
AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]
LOGIN_URL = "cuentas:ingresar"
LOGIN_REDIRECT_URL = "inicio"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "csp.middleware.CSPMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "config.middleware.ContextoSolicitudMiddleware",
    "config.middleware.BloqueoOrigenMiddleware",
    "config.middleware.MetricasMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.cuentas.middleware.SesionMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "config.middleware.CabecerasSeguridadMiddleware",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "csp.context_processors.nonce",
                "apps.cuentas.contexto.menu",
            ],
        },
    }
]

# --- Base de datos: la aplicación se conecta con el usuario de mínimo privilegio ------------------------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("DB_NAME", "iess"),
        "HOST": env("DB_HOST", "db"),
        "PORT": env("DB_PORT", "5432"),
        "USER": env("DB_USUARIO", "iess_app"),
        "PASSWORD": env("DB_CLAVE"),
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {"connect_timeout": 5},
    }
}

# --- Caché y límites (Redis; sin datos personales) --------------------------------------------------------
_REDIS_BASE = f"redis://:{env('REDIS_CLAVE', '', requerido=False)}@{env('REDIS_HOST', 'redis')}:6379"
CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": f"{_REDIS_BASE}/0"},
    "limites": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": f"{_REDIS_BASE}/1"},
}

# --- Claves: Argon2id (argon2-cffi usa Type.ID) -------------------------------------------------------------
PASSWORD_HASHERS = ["django.contrib.auth.hashers.Argon2PasswordHasher"]

# --- Sesiones y cookies ---------------------------------------------------------------------------------------
SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_SERIALIZER = "django.contrib.sessions.serializers.JSONSerializer"
SESSION_COOKIE_NAME = "__Host-sessionid"
SESSION_COOKIE_AGE = P.sesion_horas_totales * 3600
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_PATH = "/"
SESSION_COOKIE_DOMAIN = None  # requisito del prefijo __Host-
SESSION_SAVE_EVERY_REQUEST = False
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
CSRF_COOKIE_NAME = "__Host-csrftoken"
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_PATH = "/"
CSRF_COOKIE_DOMAIN = None
CSRF_TRUSTED_ORIGINS = [URL_BASE]
COOKIE_DISPOSITIVO = "__Host-dispositivo"

# --- Cabeceras y TLS ------------------------------------------------------------------------------------------------
SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = int(env("HSTS_SEGUNDOS", "31536000"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"  # "no-referrer" rompería la verificación Referer de CSRF sobre HTTPS
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
USE_X_FORWARDED_HOST = False  # el Host nunca se toma de una cabecera reenviada
DATA_UPLOAD_MAX_MEMORY_SIZE = P.cuerpo_maximo_bytes
DATA_UPLOAD_MAX_NUMBER_FIELDS = 100
FILE_UPLOAD_MAX_MEMORY_SIZE = P.cuerpo_maximo_bytes

# CSP estricta (django-csp 4.x): sin scripts ni estilos en línea; el nonce queda disponible si hiciera falta.
from csp.constants import NONCE, NONE, SELF  # noqa: E402

CONTENT_SECURITY_POLICY = {
    "DIRECTIVES": {
        "default-src": [NONE],
        "script-src": [SELF, NONCE],
        "style-src": [SELF, NONCE],
        "img-src": [SELF],
        "font-src": [SELF],
        "connect-src": [SELF],
        "form-action": [SELF],
        "frame-ancestors": [NONE],
        "base-uri": [SELF],
        "object-src": [NONE],
        "upgrade-insecure-requests": True,
    }
}

# --- Internacionalización -------------------------------------------------------------------------------------------
LANGUAGE_CODE = "es-ec"
TIME_ZONE = "America/Guayaquil"
USE_I18N = True
USE_TZ = True

# --- Estáticos ------------------------------------------------------------------------------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# --- Parámetros propios del dominio (los números viven en config/parametros.py) -------------------------------------
HMAC_TOKENS_CLAVE = env("HMAC_TOKENS_CLAVE").encode()  # codifica códigos de activación y tokens en la base
# Cifrado en reposo del secreto TOTP (Fernet; la primera clave cifra, todas descifran -> rotación).
TOTP_CLAVES_CIFRADO = env_lista("TOTP_CLAVES_CIFRADO", requerido=True)
# Cadena de auditoría: HMAC-SHA256 con clave propia (separada de SECRET_KEY) y versionada.
AUDITORIA_CLAVES = {
    int(version): clave.encode()
    for version, clave in (par.split("=", 1) for par in env_lista("AUDITORIA_CLAVES", requerido=True))
}
AUDITORIA_VERSION_ACTUAL = int(env("AUDITORIA_VERSION_ACTUAL", str(max(AUDITORIA_CLAVES))))

NOTIFICACIONES_ADAPTADOR = env("NOTIFICACIONES_ADAPTADOR", "consola")  # consola | archivo | memoria
NOTIFICACIONES_DIRECTORIO = env("NOTIFICACIONES_DIRECTORIO", str(BASE_DIR / "var" / "bandeja"), requerido=False)
AFILIACION_ADAPTADOR = env("AFILIACION_ADAPTADOR", "http")  # http | memoria
AFILIACION_URL = env("AFILIACION_URL", "http://afiliacion_sim:9001", requerido=False)
SERVICIOS_CLAVE_AFILIACION = env("SERVICIOS_CLAVE_AFILIACION", "", requerido=False).encode()
AGENDA_ADAPTADOR = env("AGENDA_ADAPTADOR", "http")  # http | memoria
AGENDA_URL = env("AGENDA_URL", "http://agenda_sim:9002", requerido=False)
SERVICIOS_CLAVE_AGENDA = env("SERVICIOS_CLAVE_AGENDA", "", requerido=False).encode()
CLAVES_FILTRADAS_ARCHIVO = BASE_DIR / "config" / "datos" / "claves_filtradas.txt"
# IP del cliente: el proxy sobrescribe X-Forwarded-For con $remote_addr (un solo valor).
IP_CLIENTE_CABECERA = env("IP_CLIENTE_CABECERA", "HTTP_X_FORWARDED_FOR", requerido=False)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    # Formateador seguro: redacta claves, tokens, cookies y números de 10 dígitos en TODO registro (R08).
    "formatters": {"simple": {"()": "config.logging_redaccion.FormateadorSeguro", "format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
    "handlers": {"consola": {"class": "logging.StreamHandler", "formatter": "simple"}},
    "root": {"handlers": ["consola"], "level": "INFO"},
    "loggers": {"django.request": {"level": "WARNING"}},
}

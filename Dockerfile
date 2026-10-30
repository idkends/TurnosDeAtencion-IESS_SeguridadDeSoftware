# syntax=docker/dockerfile:1
# --- Compilación: instala dependencias con verificación de hashes en un entorno virtual ---------------------------
FROM python:3.13-slim-bookworm AS base-deps
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
COPY requirements.txt /tmp/requirements.txt
RUN /opt/venv/bin/pip install --require-hashes --no-deps -r /tmp/requirements.txt

# --- Dependencias de prueba (solo para el perfil "pruebas"; nunca llega a la imagen final) -----------------------
FROM base-deps AS dev-deps
COPY requirements-dev.txt /tmp/requirements-dev.txt
RUN /opt/venv/bin/pip install --require-hashes --no-deps -r /tmp/requirements-dev.txt

# --- Imagen de pruebas ----------------------------------------------------------------------------------------------
FROM python:3.13-slim-bookworm AS pruebas
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/opt/venv/bin:$PATH"
COPY --from=dev-deps /opt/venv /opt/venv
RUN useradd --system --uid 10001 --create-home app
WORKDIR /app
USER app

# --- Imagen de ejecución: sin pip, sin compiladores, usuario no root --------------------------------------------
FROM python:3.13-slim-bookworm AS final
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/opt/venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=config.settings.prod
COPY --from=base-deps /opt/venv /opt/venv
RUN rm -rf /opt/venv/lib/python3.13/site-packages/pip* /opt/venv/bin/pip* \
    && useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /app/staticfiles /var/bandeja && chown -R app:app /var/bandeja
WORKDIR /app
COPY --chown=root:root manage.py ./
COPY --chown=root:root config ./config
COPY --chown=root:root apps ./apps
COPY --chown=root:root templates ./templates
COPY --chown=root:root static ./static
# collectstatic necesita variables mínimas; se usan valores desechables solo durante la compilación.
RUN DJANGO_SECRET_KEY=compilacion ALLOWED_HOSTS=localhost URL_BASE=https://localhost HMAC_TOKENS_CLAVE=x \
    TOTP_CLAVES_CIFRADO=x AUDITORIA_CLAVES=1=x DB_CLAVE=x python manage.py collectstatic --noinput \
    && chown -R root:root /app/staticfiles && chmod -R a+rX /app
USER app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=4s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request as u; r=u.Request('http://127.0.0.1:8000/salud', headers={'X-Forwarded-Proto':'https'}); u.urlopen(r, timeout=3)"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--worker-class", "gthread", "--threads", "4", "--timeout", "10", "--limit-request-line", "4094", "--access-logfile", "-"]

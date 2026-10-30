#!/usr/bin/env sh
# Genera el certificado TLS de DESARROLLO en docker/certs/ (localhost, 127.0.0.1, ::1).
# Si mkcert está instalado lo usa (el navegador confiará en él tras `mkcert -install`); si no, crea un
# certificado autofirmado con openssl dentro de un contenedor (el navegador mostrará una advertencia).
set -eu
cd "$(dirname "$0")"
if command -v mkcert >/dev/null 2>&1; then
  mkcert -cert-file localhost.pem -key-file localhost-key.pem localhost 127.0.0.1 ::1
else
  echo "mkcert no está instalado: se usa un certificado autofirmado de desarrollo."
  docker run --rm --entrypoint sh -v "$(pwd):/certs" -w /certs alpine/openssl -c '
    openssl req -x509 -newkey rsa:3072 -nodes -days 365 -keyout localhost-key.pem -out localhost.pem       -subj "/CN=localhost" -addext "subjectAltName=DNS:localhost,IP:127.0.0.1,IP:::1" &&
    chmod 644 localhost.pem localhost-key.pem'
fi

#!/usr/bin/env sh
# Regenera requirements.txt y requirements-dev.txt con hashes (pip-compile --generate-hashes)
# dentro de un contenedor con Python 3.13, para que coincidan con la imagen de la aplicación.
set -eu
docker run --rm -v "$(pwd):/w" -w /w python:3.13-slim-bookworm sh -c '
  pip install --quiet pip-tools &&
  pip-compile --quiet --generate-hashes --strip-extras --allow-unsafe --output-file requirements.txt requirements.in &&
  pip-compile --quiet --generate-hashes --strip-extras --allow-unsafe --output-file requirements-dev.txt requirements-dev.in
'

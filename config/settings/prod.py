"""Producción. Idéntica a la base: nada se relaja. Aquí solo se verifica que no se haya debilitado por error."""
from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403

# Excepciones y no `assert`: un assert desaparece con `python -O` y la comprobación dejaría de existir.
if DEBUG is not False:  # noqa: F405
    raise ImproperlyConfigured("DEBUG debe ser False en producción.")
if SECURE_SSL_REDIRECT is not True:  # noqa: F405
    raise ImproperlyConfigured("SECURE_SSL_REDIRECT debe ser True en producción.")

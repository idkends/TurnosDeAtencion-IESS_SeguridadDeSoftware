"""Producción. Idéntica a la base: nada se relaja. Aquí solo se verifica que no se haya debilitado por error."""
from .base import *  # noqa: F401,F403

assert DEBUG is False  # noqa: F405
assert SECURE_SSL_REDIRECT is True  # noqa: F405

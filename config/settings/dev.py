"""Desarrollo local (Docker Compose con proxy HTTPS). Relaja solo lo imprescindible para depurar."""
from .base import *  # noqa: F401,F403

DEBUG = env("DJANGO_DEBUG", "0", requerido=False) == "1"  # noqa: F405

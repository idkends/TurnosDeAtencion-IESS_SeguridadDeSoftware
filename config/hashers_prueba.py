"""Solo para pruebas: Argon2id con costos bajos. Producción usa los valores por defecto de argon2-cffi."""
from django.contrib.auth.hashers import Argon2PasswordHasher


class Argon2IdRapido(Argon2PasswordHasher):
    time_cost = 1
    memory_cost = 512
    parallelism = 1

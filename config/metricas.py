"""Métricas de solicitudes en Redis (por minuto): total, errores 5xx y un histograma de latencia (p95).

Sin datos personales ni rutas: solo contadores. Las lee la detección (D12, D13) y las alimenta el middleware.
"""
import time

from django.core.cache import caches

from config import limites

LIMITES_LATENCIA = (0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0)  # segundos; el último cubo es "más de 10 s"


def _cache():
    return caches["limites"]


def _minuto(ts: float | None = None) -> int:
    return int((ts if ts is not None else time.time()) // 60)


def registrar(duracion_s: float, estado: int, ts: float | None = None) -> None:
    m = _minuto(ts)
    cubo = next((i for i, limite in enumerate(LIMITES_LATENCIA) if duracion_s <= limite), len(LIMITES_LATENCIA))
    claves = [f"met:tot:{m}", f"met:lat:{m}:{cubo}"] + ([f"met:5xx:{m}"] if estado >= 500 else [])
    limites.incrementar("limites", claves, 3600)


def resumen(ventana_segundos: int, ahora: float | None = None) -> dict:
    """Totales de la ventana: solicitudes, 5xx, porcentaje de 5xx y p95 de latencia (cota superior del cubo)."""
    ahora = ahora if ahora is not None else time.time()
    minutos = range(_minuto(ahora) - max(1, ventana_segundos // 60) + 1, _minuto(ahora) + 1)
    cache = _cache()
    total = sum(cache.get(f"met:tot:{m}", 0) for m in minutos)
    cinco = sum(cache.get(f"met:5xx:{m}", 0) for m in minutos)
    cubos = [sum(cache.get(f"met:lat:{m}:{i}", 0) for m in minutos) for i in range(len(LIMITES_LATENCIA) + 1)]
    p95 = 0.0
    if total:
        objetivo, acumulado = 0.95 * total, 0
        for i, n in enumerate(cubos):
            acumulado += n
            if acumulado >= objetivo:
                p95 = LIMITES_LATENCIA[i] if i < len(LIMITES_LATENCIA) else float(LIMITES_LATENCIA[-1]) * 2
                break
    return {"total": total, "errores_5xx": cinco, "porcentaje_5xx": (100.0 * cinco / total) if total else 0.0, "p95": p95}

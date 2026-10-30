"""Interruptor de circuito: tras N fallos dentro de una ventana, rechaza llamadas durante una pausa."""
import threading
import time
from collections import deque


class Circuito:
    def __init__(self, fallos: int, ventana_segundos: float, pausa_segundos: float, reloj=time.monotonic):
        self._max_fallos = fallos
        self._ventana = ventana_segundos
        self._pausa = pausa_segundos
        self._reloj = reloj
        self._fallos: deque[float] = deque()
        self._abierto_hasta = 0.0
        self._cerrojo = threading.Lock()

    def abierto(self) -> bool:
        with self._cerrojo:
            return self._reloj() < self._abierto_hasta

    def registrar_fallo(self) -> None:
        with self._cerrojo:
            ahora = self._reloj()
            self._fallos.append(ahora)
            while self._fallos and ahora - self._fallos[0] > self._ventana:
                self._fallos.popleft()
            if len(self._fallos) >= self._max_fallos:
                self._abierto_hasta = ahora + self._pausa
                self._fallos.clear()

    def registrar_exito(self) -> None:
        with self._cerrojo:
            self._fallos.clear()

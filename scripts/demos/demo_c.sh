#!/usr/bin/env bash
# Demo c — Doble reserva por concurrencia (M04, R13)
# Falla INTENCIONAL (solo en la rama lab/inseguro, nunca en main): sin bloqueo de fila ni índice único: dos personas obtienen la misma franja
. "$(dirname "$0")/comun.sh"
correr_demo c "Doble reserva por concurrencia (M04, R13)" "sin bloqueo de fila ni índice único: dos personas obtienen la misma franja"

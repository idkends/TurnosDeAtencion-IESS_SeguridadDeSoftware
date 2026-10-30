#!/usr/bin/env bash
# Demo b — IDOR: cambiar el identificador de /turnos/{id} (M03, R15)
# Falla INTENCIONAL (solo en la rama lab/inseguro, nunca en main): /turnos/{id} no comprueba que el turno sea de quien lo pide
. "$(dirname "$0")/comun.sh"
correr_demo b "IDOR: cambiar el identificador de /turnos/{id} (M03, R15)" "/turnos/{id} no comprueba que el turno sea de quien lo pide"

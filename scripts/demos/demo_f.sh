#!/usr/bin/env bash
# Demo f — Enlace de recuperación envenenado con la cabecera Host (M01, R20)
# Falla INTENCIONAL (solo en la rama lab/inseguro, nunca en main): el enlace se arma con la cabecera Host y se aceptan todos los Host
. "$(dirname "$0")/comun.sh"
correr_demo f "Enlace de recuperación envenenado con la cabecera Host (M01, R20)" "el enlace se arma con la cabecera Host y se aceptan todos los Host"

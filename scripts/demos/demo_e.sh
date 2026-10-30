#!/usr/bin/env bash
# Demo e — Servicio de afiliación caído y cobertura falsa (M08, R07)
# Falla INTENCIONAL (solo en la rama lab/inseguro, nunca en main): si el tercero no responde, el portal da por buena la afiliación y confirma el turno
. "$(dirname "$0")/comun.sh"
correr_demo e "Servicio de afiliación caído y cobertura falsa (M08, R07)" "si el tercero no responde, el portal da por buena la afiliación y confirma el turno"

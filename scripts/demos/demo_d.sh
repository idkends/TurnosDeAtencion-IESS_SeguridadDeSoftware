#!/usr/bin/env bash
# Demo d — Registro de auditoría editable (M07, R22)
# Falla INTENCIONAL (solo en la rama lab/inseguro, nunca en main): la base permite UPDATE y DELETE sobre la auditoría (sin disparador ni permisos de solo adición)
. "$(dirname "$0")/comun.sh"
correr_demo d "Registro de auditoría editable (M07, R22)" "la base permite UPDATE y DELETE sobre la auditoría (sin disparador ni permisos de solo adición)"

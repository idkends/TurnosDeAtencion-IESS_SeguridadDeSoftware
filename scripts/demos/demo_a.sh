#!/usr/bin/env bash
# Demo a — Clave inicial = fecha de nacimiento y mensajes de login distintos (M01, R05/R18)
# Falla INTENCIONAL (solo en la rama lab/inseguro, nunca en main): la cuenta nueva usa la fecha de nacimiento como clave y el login dice «la cédula no existe» / «clave incorrecta»
. "$(dirname "$0")/comun.sh"
correr_demo a "Clave inicial = fecha de nacimiento y mensajes de login distintos (M01, R05/R18)" "la cuenta nueva usa la fecha de nacimiento como clave y el login dice «la cédula no existe» / «clave incorrecta»"

"""Datos de trazabilidad copiados del informe (Anexo C, §10.1, §10.2 y Anexo G). Fuente: docs/informe.pdf."""

# Requisito -> (decisión, riesgos, controles, pruebas T, módulos)  [Anexo C.1]
REQUISITOS = {
    "I01": ("Conservar", "R12 R13 R14 R15 R21", "RT-01 a RT-12; restricciones únicas; idempotencia", "T07 T09 T13 T16 T17", "M04"),
    "I02": ("Modificar", "R18", "Datos mínimos; la fecha de nacimiento no autentica", "T03", "M01"),
    "I03": ("Eliminar", "R01", "No se recolecta", "T01", "M04"),
    "I04": ("Eliminar", "R02", "Agenda separada de lo clínico", "T01", "M04"),
    "I05": ("Eliminar", "R01", "No se recolecta", "T01", "M04"),
    "I06": ("Modificar", "R04 R15", "Delegación con alcance y vigencia", "T02 T13", "M05"),
    "I07": ("Modificar", "R05 R18 R19 R20", "Activación, Argon2id, segundo factor, bloqueo", "T03 T12", "M01 M02"),
    "I08": ("Modificar", "R06", "RBAC, relación asistencial, autorización por objeto", "T04", "M03 M14"),
    "I09": ("Modificar", "R03 R20", "Mensajes neutrales; tokens de un solo uso", "T05 T12", "M06"),
    "I10": ("Conservar", "R07", "Autenticación entre servicios; PENDIENTE; circuito abierto", "T07", "M08"),
    "I11": ("Modificar", "R08 R22", "Solo adición; hash encadenado; sin secretos", "T06 T11b", "M07 M14"),
    "I12": ("Modificar", "R09 R21", "Separación, redundancia, respaldos", "T08 T09", "M13"),
    "I13": ("Modificar", "R10", "Despliegue gradual; SLO", "T08", "M13"),
    "I14": ("Modificar", "R11", "Plazos; sin entrega para IA; contratos", "T10 T11", "M12"),
}

# Riesgo -> (control preventivo principal, pruebas T, módulos)  [Anexo C.2]
RIESGOS = {
    "R01": ("No recolectar: esquema sin esos campos", "T01", "M04 M12"),
    "R02": ("Separar agenda y sistema clínico", "T01", "M04"),
    "R03": ("Mensajes neutrales; enlace de un solo uso que exige sesión", "T05", "M06"),
    "R04": ("Delegación con alcance, vigencia y revocación", "T02", "M05"),
    "R05": ("Activación de un solo uso; Argon2id; bloqueo; segundo factor", "T03", "M01 M02"),
    "R06": ("RBAC + relación asistencial; denegar por defecto", "T04", "M03 M14"),
    "R07": ("Autenticación entre servicios, tiempos de espera, circuito abierto", "T07", "M08"),
    "R08": ("Lista blanca de campos y redacción de secretos", "T06", "M07"),
    "R09": ("Separación, redundancia y respaldos", "T08", "M13"),
    "R10": ("Despliegues graduales sin interrupción", "T08", "M13"),
    "R11": ("Sin entrega para IA; plazos de conservación", "T10 T11", "M12"),
    "R12": ("RT-01..RT-04, RT-08, RT-09; anti-automatización", "T16", "M04 M09"),
    "R13": ("Índice único parcial, bloqueo de fila, idempotencia", "T07", "M04"),
    "R14": ("Confirmación, liberación, bloqueo proporcional y apelación", "T17", "M04"),
    "R15": ("Autorización por objeto e identificadores no secuenciales", "T13", "M03"),
    "R16": ("ORM parametrizado, autoescape, CSRF, CSP", "T14", "M10"),
    "R17": ("Versiones LTS, pip-audit, SBOM, parcheo", "T15", "M11"),
    "R18": ("Mensajes genéricos, tiempos constantes, límites por origen", "T03", "M01 M09"),
    "R19": ("Sesión 24 h / 1 h de inactividad, cookies seguras, revocación", "T03", "M02"),
    "R20": ("Dominios oficiales, no pedir claves, segundo factor", "T12", "M01 M02"),
    "R21": ("Límites de solicitudes, caché, sala de espera", "T09", "M09"),
    "R22": ("Solo adición, hash encadenado, copia externa", "T11b", "M07"),
    "R23": ("Plan de respuesta, responsables, notificación", "T12", "M07 M14"),
}

# Pruebas T01..T18 -> (qué verifica, riesgos)  [§10.1]
PRUEBAS = {
    "T01": ("Reservar sin datos laborales/religiosos/de salud; esquema sin esos campos", "R01 R02"),
    "T02": ("Cédula ajena sin delegación, delegación revocada y válida", "R04"),
    "T03": ("Clave biográfica, activación reutilizada, bloqueo, cédula inexistente, expiración y revocación de sesión", "R05 R18 R19"),
    "T04": ("Matriz de permisos por rol; denegaciones auditadas", "R06"),
    "T05": ("Notificación neutral; enlace sin datos que pide autenticación", "R03"),
    "T06": ("Contraseñas, tokens y diagnóstico no aparecen en los registros", "R08"),
    "T07": ("Concurrencia por la última franja; idempotencia; servicio externo lento", "R07 R13"),
    "T08": ("Restaurar un respaldo con RPO/RTO medidos; despliegue sin perder reservas", "R09 R10"),
    "T09": ("Carga de 200 req/s con ráfaga de 400; el límite no bloquea a todos", "R21"),
    "T10": ("Integraciones mínimas y revocables; sin exportación para entrenar IA", "R11"),
    "T11": ("Vencimiento de datos, acceso y eliminación", "R01 R11"),
    "T11b": ("Auditoría de solo adición: alteración detectada", "R22"),
    "T12": ("Simulacro de incidente y suplantación (enlaces, dominios, muchas cédulas desde una IP)", "R20 R23"),
    "T13": ("Cambiar el identificador de un turno o afiliado: 403/404 y registro", "R15"),
    "T14": ("Inyección SQL, XSS y CSRF; análisis estático", "R16"),
    "T15": ("Dependencias, SBOM y versión de Django", "R17"),
    "T16": ("Reglas de turnos: segunda reserva, tope, solape, seis representados, muchas cédulas", "R12"),
    "T17": ("Confirmación/expiración, inasistencias, justificación, apelación, prioritarios", "R14"),
    "T18": ("check --deploy, cabeceras, cookies, TLS, DEBUG y secretos", "R09"),
}

# Casos de abuso CA-01..CA-16 -> (descripción, riesgos)  [Anexo G]
CASOS = {
    "CA01": ("Reservar el turno de otra persona con su cédula", "R04"),
    "CA02": ("Acaparar cupos en la misma especialidad", "R12"),
    "CA03": ("Ganar la última franja con solicitudes simultáneas", "R13"),
    "CA04": ("Activar la cuenta de otra persona con mi correo (pre-secuestro)", "R05 R20"),
    "CA05": ("Averiguar qué cédulas son afiliadas", "R18"),
    "CA06": ("Reutilizar un enlace de restablecimiento o de aviso", "R03 R19"),
    "CA07": ("Leer el turno de otra persona cambiando el identificador", "R15"),
    "CA08": ("Inyectar SQL o scripts en login, búsqueda y apelaciones", "R16"),
    "CA09": ("Recepción que ve pacientes de otra unidad", "R06"),
    "CA10": ("Supervisor que edita un registro de auditoría", "R22"),
    "CA11": ("Usar una cookie robada pasadas 24 horas", "R19"),
    "CA12": ("Automatizar reservas con un bot", "R12 R21"),
    "CA13": ("Doble clic o reintento de una reserva", "R13"),
    "CA14": ("Afiliado bloqueado sin poder reclamar", "R14"),
    "CA15": ("Servicio de afiliación falla y se confirma cobertura falsa", "R07"),
    "CA16": ("Envenenar un enlace de restablecimiento con la cabecera Host", "R17 R20"),
}

# Módulos del prototipo  [§10.2]
MODULOS = {
    "M01": "Cuentas y activación", "M02": "Sesiones y segundo factor", "M03": "Autorización", "M04": "Turnos y reglas",
    "M05": "Delegaciones", "M06": "Notificaciones neutrales", "M07": "Auditoría de solo adición", "M08": "Integraciones simuladas",
    "M09": "Límites y antiautomatización", "M10": "Cabeceras, CSRF y configuración", "M11": "Dependencias e integración continua",
    "M12": "Retención y derechos", "M13": "Respaldos y restauración", "M14": "Paneles de auditor y DPD",
}

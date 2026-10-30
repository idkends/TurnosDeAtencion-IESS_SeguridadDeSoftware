# Revisión final (Etapa 4)

Se repasó el código contra (1) la lista de verificación de código del Anexo H, (2) OWASP Top 10:2025 y OWASP API Security Top 10:2023 (tablas del informe) y
(3) las reglas de arquitectura de las reglas del proyecto (no publicadas). Cada afirmación de «cumple» tiene detrás una prueba automática o un comando que se puede repetir
(`make verify`); lo que no se pudo comprobar así se dice como tal.

## 1. Lista de verificación de código (Anexo H)

| # | Pregunta | Resultado | Cómo se comprobó |
|---|---|---|---|
| 1 | Rutas en el inventario con autorización por objeto | **Cumple** | `test_inventario.py` (falla con una ruta fuera de `config/inventario.py`); `test_T13_*` y `test_CA07_*` |
| 2 | Sin SQL crudo ni `**request.GET` | **Cumple, con 2 excepciones justificadas** | `grep` en `apps/` y `config/`: solo `pg_advisory_xact_lock(%s)` parametrizado en auditoría y delegaciones; Bandit sin hallazgos medios/altos |
| 3 | Formularios con campos explícitos | **Cumple** | Ningún `fields = '__all__'`; los esquemas heredan de `FormularioEstricto` (campos desconocidos rechazados, `test_API3_*`) |
| 4 | Plantillas sin contenido de usuario «seguro» | **Cumple, 1 excepción** | Único `mark_safe`: el SVG del QR de TOTP que genera `segno` a partir del URI propio (marcado `# nosec` con justificación) |
| 5 | POST + CSRF + idempotencia en lo que cambia estado | **Cumple** | `despachar(GET=…, POST=…)`, `test_T14_R16_el_csrf_*`, idempotencia en `reservar` (`test_CA13_*`); ninguna vista usa `csrf_exempt` |
| 6 | Mensajes de error y login genéricos | **Cumple** | `test_CA05_*` (mensaje y tiempo indistinguibles), demostración A |
| 7 | Registros sin contraseñas, tokens ni datos clínicos | **Cumple** | `test_T06_*` (formateador y flujos completos), proxy con rutas enmascaradas |
| 8 | Secretos desde la configuración | **Cumple** | `test_ningun_secreto_literal_*`; gitleaks sobre los 16+ commits del historial: sin hallazgos |
| 9 | Restricción en la base y prueba concurrente por cada regla de turnos | **Cumple** | `test_restricciones_bd.py`; `test_T07_mil_intentos…`, `test_CA03_*`, `test_CA13_*` |
| 10 | Dependencias con soporte y sin CVE críticos | **Cumple** | pip-audit: 0 vulnerabilidades en 17 paquetes (hashes `--require-hashes`); Django 5.2.18 (LTS); SBOM CycloneDX |

## 2. OWASP Top 10:2025

| Categoría | Estado | Evidencia |
|---|---|---|
| A01 Broken Access Control | Cumple | `cuentas/politicas.py` único; 404 idéntico; `test_T04_*` (matriz de roles), `test_CA09_*` |
| A02 Security Misconfiguration | Cumple | `check --deploy` sin advertencias; cabeceras/cookies/TLS (24 comprobaciones contra la app viva); contenedores endurecidos |
| A03 Software Supply Chain Failures | Cumple | Hashes, imágenes con resumen, SBOM, pip-audit, gitleaks |
| A04 Cryptographic Failures | Cumple | TLS 1.2+ (1.0/1.1 rechazados), Argon2id, Fernet (TOTP), HMAC-SHA256 con clave versionada (auditoría) |
| A05 Injection | Cumple | ORM parametrizado; autoescape + CSP con nonce; Bandit y ZAP sin altos ni medios (`test_CA08_*`) |
| A06 Insecure Design | Cumple | RT-01…RT-12 con restricciones en la base; casos de abuso CA-01..CA-16 probados |
| A07 Authentication Failures | Cumple | Bloqueo progresivo, TOTP, sesiones 24 h / 1 h, mensajes y tiempos genéricos |
| A08 Software or Data Integrity Failures | Cumple con una limitación | Auditoría encadenada y de solo adición; **no detecta el truncado del final** (limitación 1) |
| A09 Security Logging and Alerting Failures | Cumple | Logs sin secretos + detección D01-D18 con acciones automáticas |
| A10 Mishandling of Exceptional Conditions | Cumple | Errores genéricos; PENDIENTE ante fallo del tercero; circuito abierto |

## 3. OWASP API Security Top 10:2023

| Riesgo | Estado | Evidencia |
|---|---|---|
| API1 Autorización a nivel de objeto | Cumple | `test_T13_*`, `test_CA07_*`, UUID no secuenciales |
| API2 Autenticación rota | Cumple | `test_T03_*`, `test_CA04_*`…`CA06_*` |
| API3 Autorización a nivel de propiedad | Cumple | `test_API3_*`: el cliente no puede enviar estado ni propietario |
| API4 Consumo de recursos sin límite | Cumple | Límites por ruta/IP/cuenta/dispositivo, cuerpo ≤ 64 KB, paginación; carga k6 |
| API5 Autorización a nivel de función | Cumple | Matriz de roles (`test_T04_*`), rutas `/interno` con rol, unidad y segundo factor |
| API6 Flujos de negocio sensibles | Cumple | RT-01…RT-12, límite 10 reservas/hora por cuenta, CA-02, CA-12 |
| API7 SSRF | Cumple | El portal no recibe URL del cliente; destinos fijos de integraciones |
| API8 Configuración incorrecta | Cumple | `check --deploy`, CORS cerrado (WhiteNoise sin `*`), errores genéricos |
| API9 Gestión del inventario | Cumple | `docs/endpoints.md` generado de `config/inventario.py`; la prueba falla con rutas nuevas sin inventariar |
| API10 Consumo inseguro de APIs | Cumple | Esquema validado, tiempos de espera, PENDIENTE (`test_CA15_*`) |

## 4. Reglas de arquitectura

| Regla | Estado | Cómo se vigila |
|---|---|---|
| Vistas finas: sin reglas de negocio ni ORM | **Cumple** | `test_las_vistas_son_finas_*` (AST) + `grep` sin `.objects`/`.save` en vistas |
| Reglas y escrituras solo en `servicios.py` | **Cumple** | `test_las_escrituras_al_orm_solo_ocurren_en_servicios` |
| Permisos solo en `politicas.py` (puro, sin ORM ni I/O) | **Cumple** | `test_las_politicas_son_puras`, `test_solo_el_modulo_de_politicas_decide_permisos…`; sin `usuario.rol` en vistas |
| Auditoría solo en `auditoria` | **Cumple** | `test_solo_auditoria_conoce_su_tabla`, trigger y permisos en la base |
| Terceros solo por adaptadores | **Cumple** | `test_el_dominio_no_conoce_clientes_de_red_solo_los_adaptadores` |
| Apps solo importan `servicios` de las demás; sentido único | **Cumple** | 14 contratos de import-linter + AST |
| Parámetros de seguridad y de turnos en un único módulo | **Cumple en lo sustantivo; parcial en lo técnico** | Todos los parámetros del Anexo E y las ventanas de detección están en `config/parametros.py` (esta revisión movió 11 que seguían en el código). Quedan constantes técnicas sueltas (códigos HTTP, truncados de texto, tamaño de lotes, horas de la siembra): **no hay prueba automática** que lo vigile (limitación 11) |
| Plantillas sin lógica ni consultas | **Cumple** | `test_las_plantillas_no_tienen_logica_ni_consultas…` |

## 5. Hallazgos de esta etapa (y qué se hizo)

| Hallazgo | Acción |
|---|---|
| La prueba de carga **no cumplía** la meta al principio (136 req/s efectivos, p95 de 7,6 s) | Más procesos de gunicorn, caché de catálogos y de la tabla pública de disponibilidad, 15 → 7 viajes a Redis por solicitud (contadores atómicos). Resultado final: p95 de 14–140 ms a 200 req/s y 1,9 s en la ráfaga de 400 (justo en la meta) |
| ZAP: CSP/HSTS ausentes en las respuestas de error que genera nginx; CORS `*` y sin `Permissions-Policy` en estáticos; sin COEP | Cabeceras en las respuestas del proxy, `WHITENOISE_ALLOW_ALL_ORIGINS=False`, cabeceras en estáticos, `Cross-Origin-Embedder-Policy`. Resultado: 0 altos, 0 medios, 0 bajos |
| Bandit: 7 medios y 3 bajos | `assert` de `prod.py` reemplazados por excepciones; resto justificado con `# nosec` y motivo |
| gitleaks marcaba cadenas ficticias en pruebas | Lista de excepciones acotada a la regla genérica y a carpetas `tests/` (`.gitleaks.toml`) |
| `rotar_totp` no era idempotente; la redacción de logs dejaba pasar `totp_secreto=` y `Authorization: Bearer …` (Etapa 3) | Corregidos con prueba |
| Nombres de pruebas T10/T11 no coincidían con el informe | Renombradas; la trazabilidad es ahora automática |

## 6. Lo que se simuló en lugar de implementar

Servicios de afiliación y agenda (servidores de prueba que fallan a propósito), correo y SMS (consola/archivo), TLS (certificado de desarrollo), WAF/CDN,
nodos redundantes y conmutación, gestor de secretos/KMS, SIEM, almacenamiento remoto inmutable de respaldos, escáner continuo de imágenes, controles físicos y
el cumplimiento LOPDP real (DPD, evaluación de impacto, contratos). Detalle y qué cambiaría: [`despliegue.md`](despliegue.md), [`limitaciones.md`](limitaciones.md).

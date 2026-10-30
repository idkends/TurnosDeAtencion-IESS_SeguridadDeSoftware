# Controles de seguridad por tipo y naturaleza

Tomado de la tabla §9.4 del informe (preventivo / detectivo / correctivo; naturaleza **T**écnica, **A**dministrativa, **F**ísica) y
ubicado en el código. «Prueba» es el identificador de `docs/trazabilidad.md`.

## Por requisito

| Req. | Preventivo (dónde vive) | Detectivo (dónde vive) | Correctivo (dónde vive) | Nat. | Prueba |
|---|---|---|---|---|---|
| I01 Turnos | Restricciones únicas y de exclusión en `turnos/modelos.py`; RT-01…RT-12 y bloqueo de fila en `turnos/servicios.py`; idempotencia (`SolicitudReserva`) | Conciliación con la agenda (`conciliar_agenda`); regla D06 en `deteccion/servicios.py` | Cancelar/reprogramar/deshacer, apelación humana (`apelar`) | T y A | T07, T16, T17, CA-02, CA-03, CA-13 |
| I02 Identificación | Datos mínimos; la fecha de nacimiento nunca autentica (`cuentas/claves.py`) | Enumeración: D04 | Bloqueo de origen (`config/bloqueos.py`) | T | T03, CA-05 |
| I03 Laboral · I05 Religión | No se recolecta: el esquema no tiene esos campos | Prueba de esquema | Eliminación segura (`derechos/servicios.py`) | A y T | T01 |
| I04 Salud | Agenda separada de lo clínico (`turnos/modelos.py` sin campos clínicos) | Prueba de esquema | Purga de datos copiados (`ejecutar_vencimiento`) | A y T | T01 |
| I06 Familiares | Delegación con alcance, vigencia y revocación (`delegaciones/servicios.py`) | Aviso al titular (`notificaciones`); auditoría | Revocar y restaurar el turno | T y A | T02, CA-01 |
| I07 Autenticación | Activación de un solo uso, Argon2id, TOTP, bloqueo progresivo, sesiones de 24 h / 1 h (`cuentas/servicios.py`, `config/settings/base.py`) | D01-D03 y D05 (dispositivo nuevo) | Revocar sesiones; restablecer por enlace de un solo uso | T y A | T03, CA-04, CA-05, CA-06, CA-11 |
| I08 Permisos | RBAC + relación asistencial + autorización por objeto en **un solo módulo** (`cuentas/politicas.py`); denegar por defecto | Auditoría de denegaciones; D07 (sondeo de identificadores), D08 (consultas masivas) | Retirar privilegios; D18 avisa de toda alta o cambio de rol | T y A | T04, T13, CA-07, CA-09 |
| I09 Notificaciones | Plantillas neutrales y tokens de 128 bits de un solo uso que redirigen a iniciar sesión (`notificaciones/servicios.py`) | D17 (token usado desde otros orígenes) | Invalidar el token | T y A | T05, CA-06 |
| I10 Integración | Autenticación entre servicios, tiempos de espera, reintentos y circuito abierto en los adaptadores (`integraciones/adaptadores/`) | Monitoreo de latencia (`config/metricas.py`, D12/D13) | Modo PENDIENTE y conciliación | T y A | T07, CA-15 |
| I11 Auditoría | Solo adición (disparador + permisos), HMAC encadenado con clave versionada, sin secretos (`auditoria/servicios.py`, `config/logging_redaccion.py`) | `verificar_auditoria`; D11 | Correcciones como asientos nuevos con segundo supervisor | T y A | T06, T11b, CA-10 |
| I12 Infraestructura | Redes por zonas, contenedores endurecidos, respaldos cifrados con WAL (`docker-compose.yml`, `scripts/respaldo.sh`) | D15 (respaldo fallido / sin prueba de restauración) | Restauración con RPO/RTO medidos (`scripts/restaurar.sh`) | T, A y F | T08, T18 |
| I13 Disponibilidad | Despliegue gradual, límites, sala de espera (`config/endpoints.py`, `config/bloqueos.py`) | D12, D13, D14 | Sala de espera / escalar | T y A | T08, T09 |
| I14 Conservación e IA | Plazos de retención y sin exportación masiva (`derechos/servicios.py`, `config/parametros.py`) | Registro de exportaciones puntuales | Constancia de eliminación | A y T | T10, T11 |

## Por tipo de control (transversales)

| Tipo | Control | Código |
|---|---|---|
| Preventivo | Validación de entrada con esquemas estrictos (campos desconocidos rechazados) | `config/formularios.py`, `apps/*/esquemas.py` |
| Preventivo | CSRF, CSP con nonce, cabeceras, cookies `__Host-` | `config/settings/base.py`, `config/middleware.py`, `docker/nginx/` |
| Preventivo | Límites por ruta, IP, cuenta y dispositivo (Redis) | `config/limites.py`, `config/endpoints.py`, `config/inventario.py` |
| Preventivo | Inventario de rutas obligatorio (`@endpoint("Exx")`) | `config/inventario.py`, `tests/seguridad/test_inventario.py` |
| Preventivo | Reglas de arquitectura que fallan si se rompen | `tests/arquitectura/`, `.importlinter` |
| Preventivo | Dependencias fijadas con hashes, imágenes con resumen | `requirements*.txt`, `docker-compose.yml`, `Dockerfile` |
| Detectivo | Detección D01-D18 con severidades y acciones automáticas | `apps/deteccion/servicios.py` |
| Detectivo | Verificación automática: Bandit, pip-audit, SBOM, gitleaks, ZAP | `scripts/verify.sh`, `scripts/zap.sh` |
| Correctivo | Bloqueo de origen, desafío adaptativo, sala de espera | `config/bloqueos.py`, `config/desafio.py` |
| Correctivo | Rotación de claves sin interrupción | `docs/claves.md`, `manage.py rotar_totp` |
| Administrativo | Plan de respuesta, simulacros, flujo Git, lista de verificación de código | `docs/respuesta_incidentes.md`, `docs/flujo-git.md` |
| Físico | (Fuera del prototipo) acceso físico a los centros de datos y UPS | `docs/despliegue.md` |

## Cobertura OWASP (resumen; la tabla completa es el Anexo D.1 del informe)

| Categoría | Cómo se cubre | Prueba |
|---|---|---|
| Top 10:2025 A01 Broken Access Control | `cuentas/politicas.py`, 404 idéntico para ajeno e inexistente | T02, T04, T13, CA-07, CA-09 |
| A02 Security Misconfiguration | `check --deploy` en `verify`, cabeceras, DEBUG apagado, contenedores endurecidos | T18 |
| A03 Software Supply Chain | Hashes, SBOM, pip-audit, imágenes fijadas por resumen | T15 |
| A04 Cryptographic Failures | TLS 1.2+, Argon2id, Fernet para TOTP, HMAC de auditoría | T03, T18 |
| A05 Injection | ORM parametrizado, autoescape, CSP; Bandit y ZAP | T14, CA-08 |
| A06 Insecure Design | RT-01…RT-12 y restricciones en la base | T07, T16, T17 |
| A07 Authentication Failures | Bloqueo progresivo, TOTP, mensajes y tiempos genéricos | T03, CA-04, CA-05 |
| A08 Integrity Failures | Auditoría encadenada; hashes de dependencias | T11b, CA-10 |
| A09 Logging & Alerting Failures | Logs sin secretos + detección D01-D18 | T06 |
| A10 Mishandling of Exceptional Conditions | Errores genéricos, PENDIENTE ante fallo del tercero | T07, CA-15 |
| API Top 10:2023 (API1…API10) | Inventario `docs/endpoints.md`; esquemas estrictos; límites; adaptadores | T13, T09, T07 |

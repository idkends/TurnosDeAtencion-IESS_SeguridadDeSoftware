# Gestión de claves y secretos (Anexo J)

Ningún secreto vive en el repositorio. Todo llega por variables de entorno (`.env` local, ignorado por git; `.env.example`
sin valores). `python scripts/generar_env.py` crea un `.env` con valores aleatorios.

| Secreto | Variable | Uso | Rotación | Cómo se prueba |
|---|---|---|---|---|
| Clave de Django | `DJANGO_SECRET_KEY` (+ `DJANGO_SECRET_KEY_FALLBACKS`) | Firma de sesiones, CSRF, mensajes | Poner la nueva en `DJANGO_SECRET_KEY` y la anterior en `..._FALLBACKS`; reiniciar; retirar la anterior tras 24 h (la vida máxima de una sesión) | `test_rotacion_de_SECRET_KEY_con_fallbacks…` |
| Cifrado del secreto TOTP | `TOTP_CLAVES_CIFRADO` (lista, la primera cifra) | Fernet/MultiFernet sobre `totp_secreto_cifrado` | 1) anteponer la clave nueva; 2) `manage.py rotar_totp` recifra todo (idempotente, audita `CLAVE_ROTADA`); 3) retirar la antigua | `test_rotacion_de_la_clave_de_cifrado_del_totp` |
| Cadena de auditoría | `AUDITORIA_CLAVES` (`1=…,2=…`) y `AUDITORIA_VERSION_ACTUAL` | HMAC-SHA256 de cada asiento; cada fila guarda su versión | Añadir `N+1`, subir `AUDITORIA_VERSION_ACTUAL`. **Nunca** retirar una versión mientras existan asientos firmados con ella: `verificar_auditoria` lo detecta ("versión de clave desconocida") y deja `VERIFICACION_AUDITORIA_FALLIDA` (regla D11) | `test_rotacion_de_la_clave_de_auditoria…` |
| HMAC de tokens | `HMAC_TOKENS_CLAVE` | Resumen de tokens y códigos guardados en la base | Invalida los tokens vigentes (caducan en ≤ 24 h): rotar fuera de horario | `apps/cuentas/tests` |
| Roles de base de datos | `DB_CLAVE`, `DB_MIGRACIONES_CLAVE`, `DB_AUDITORIA_RO_CLAVE`, `DB_CLAVE_PRUEBAS`, `POSTGRES_PASSWORD` | Mínimo privilegio (ver docs/arquitectura.md) | `ALTER ROLE … PASSWORD` y actualizar `.env`; reiniciar app y tareas | `tests/seguridad/test_roles_bd.py` |
| Redis | `REDIS_CLAVE` | Contraseña de Redis (sin datos personales) | Cambiar y reiniciar `redis`, `app`, `tareas` | — |
| Servicios simulados | `SERVICIOS_CLAVE_AFILIACION`, `SERVICIOS_CLAVE_AGENDA` | Token de servicio firmado | Cambiar en ambos lados a la vez | `apps/integraciones/tests` |
| Respaldos | `RESPALDO_CLAVE` | Cifra con AES-256 (PBKDF2) las copias base | Las copias antiguas se descifran con la clave con que se hicieron: conservar la anterior mientras existan copias de ese periodo | `tests/seguridad/test_respaldos.py` |

## Reglas

- La clave de auditoría es **distinta** de `SECRET_KEY`: quien robe una no puede falsificar la cadena con la otra.
- La clave de respaldo se guarda **fuera** del volumen `respaldos`; sin ella las copias son inútiles (a propósito).
- Los registros pasan por `config.logging_redaccion.FormateadorSeguro`: contraseñas, tokens, cookies, códigos, `Authorization`,
  diagnósticos, cadenas largas y números de 10 dígitos salen como `[REDACTADO]`/`[TOKEN]`/`[ID]`.
  Las claves terminadas en `_ref` (`cedula_ref`, `token_ref`) son resúmenes no reversibles y se conservan porque la detección las necesita.
- Si una clave se filtra: rotarla de inmediato, revisar `CLAVE_ROTADA`/`VERIFICACION_AUDITORIA_FALLIDA` en la auditoría y, si era la de
  auditoría, verificar la cadena **antes** de la rotación (los asientos previos siguen siendo válidos con su versión).

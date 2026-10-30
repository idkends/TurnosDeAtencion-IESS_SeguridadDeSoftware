# Despliegue: endurecimiento local y qué cambiaría en producción

## Endurecimiento que ya tiene `docker-compose.yml` (verificado por `test_T18_*`)

| Control | Cómo |
|---|---|
| Sin root | Las imágenes de la aplicación crean y usan `app` (uid 10001, sin shell); nginx corre como `nginx` (uid 101, imagen *unprivileged*); Redis como `redis`; PostgreSQL arranca como root solo para ajustar permisos y baja a `postgres` |
| Sistema de archivos de solo lectura | `read_only: true` en todos los servicios; escritura únicamente en `tmpfs` (`/tmp`, caché de nginx, socket de PostgreSQL) y en los volúmenes declarados (`pgdata`, `wal_archivo`, `respaldos`, `bandeja`) |
| Sin nuevos privilegios | `security_opt: no-new-privileges:true` |
| Capacidades mínimas | `cap_drop: [ALL]`; solo la base de datos añade `CHOWN, DAC_OVERRIDE, FOWNER, SETGID, SETUID` por el arranque de su imagen oficial |
| Redes por zona | `perimetro` (único con puertos y salida), `aplicacion`, `datos` y `externos` son **internas**; la base y Redis no exponen puertos al host |
| Healthchecks | proxy, app (Dockerfile), tareas, base, Redis y simulados |
| Límites de recursos | `mem_limit`, `cpus` y `pids_limit` por servicio |
| Versiones fijadas | Imágenes por versión **y resumen** (`@sha256:`), incluidas las de `FROM` del Dockerfile; dependencias con hashes (`--require-hashes`) |
| Sin secretos en el repositorio | Todo por variables de entorno; `.env` ignorado; gitleaks sobre todo el historial en `make verify` |
| Procesos de la aplicación | `WEB_CONCURRENCY` (por omisión 10) ajustado con la prueba de carga T09 |

Los servicios de utilidades (`sembrar`, `pruebas`) no forman parte del despliegue y no se endurecen igual.

### Despliegue gradual (rolling)

1. Migraciones compatibles hacia atrás (añadir antes de usar; borrar en un despliegue posterior) con el servicio `migrar` (rol dueño del esquema).
2. `docker compose up -d --no-deps --scale app=2 app`: la versión nueva convive con la anterior; el proxy solo enruta a instancias sanas.
3. `--scale app=1` retira la antigua; reiniciar `tareas`. Si `/salud` falla, no se retira la anterior (reversa inmediata).

## Qué cambiaría en producción real

| Tema | Prototipo local | Producción real |
|---|---|---|
| WAF / CDN | nginx con límites por IP y la detección propia | WAF administrado delante del proxy (reglas OWASP CRS), CDN para estáticos y absorción de picos; el límite fino sigue en la aplicación |
| Nodos redundantes | Una instancia de cada cosa | Al menos 2 nodos de aplicación en zonas distintas, PostgreSQL con réplica sincrónica y conmutación automática, Redis con réplica (sin datos personales) |
| Gestor de secretos | `.env` local (ignorado por git) | Gestor (Vault/KMS/Secrets Manager) con rotación automática y acceso por identidad del servicio; claves de auditoría y de respaldo en HSM/KMS |
| SIEM | Registros a consola + módulo `deteccion` | Registros estructurados enviados a un SIEM con retención, correlación con otras fuentes y alertas de guardia 24×7 |
| TLS | Certificado de desarrollo (mkcert/autofirmado), HSTS de 60 s | Certificado de la institución, HSTS de 1 año con *preload*, mTLS entre servicios |
| Respaldos | Volumen aparte + carpeta del anfitrión | Almacenamiento remoto inmutable (objeto con *object lock*) en otra zona; prueba de restauración trimestral automatizada |
| Servicios de terceros | Afiliación y agenda simuladas | Interfaces oficiales con contrato, certificados y *allowlist* de red |
| Correo/SMS | Consola o archivo | Proveedor con contrato de encargado; SPF, DKIM y DMARC |
| Imágenes | Resumen fijado a mano | Registro privado, firma de imágenes (cosign) y escaneo continuo (Trivy/Grype) con actualización automática |
| Orquestación | Docker Compose | Kubernetes u orquestador equivalente con *NetworkPolicy*, *PodSecurity* restringido y autoescalado |
| Seguridad física | Fuera de alcance | Centro de datos con acceso controlado, UPS y generador (controles de naturaleza F del informe) |
| Cumplimiento | Datos sintéticos | LOPDP: delegado de protección de datos, evaluación de impacto aprobada, contratos con encargados, notificación a la autoridad |

# Portal de turnos IESS (prototipo académico)

Prototipo local, con datos **100 % sintéticos**, de un portal de turnos de atención médica con seguridad de nivel producción
(IESS ficticio). El diseño completo es [`docs/informe.pdf`](docs/informe.pdf) y es la fuente de verdad; las reglas del proyecto están
en [`CLAUDE.md`](CLAUDE.md). **Estado: las 4 etapas están completas** (ver [`docs/revision_final.md`](docs/revision_final.md) para lo que
cumple, lo que no y lo que se simuló).

Pila: Python 3.13 · Django 5.2 LTS · PostgreSQL 18 · Redis (solo límites y caché) · nginx con TLS de desarrollo · Docker Compose.
Todo se ejecuta en Docker: no hace falta instalar Python ni PostgreSQL.

## Arranque en 3 comandos

```sh
python scripts/generar_env.py && sh docker/certs/generar.sh   # 1. secretos aleatorios (.env, no se versiona) y certificado de desarrollo
docker compose up -d --build                                  # 2. proxy + app + PostgreSQL + Redis + servicios simulados + tareas
docker compose --profile utilidades run --rm sembrar          # 3. datos sintéticos (muestra las claves UNA sola vez)
```

Abra <https://localhost:8443> (con el certificado autofirmado el navegador avisa; con `mkcert -install` no). Los correos y SMS «enviados»
salen por la consola de la aplicación: `docker compose logs -f app`.

## Usuarios de prueba (sintéticos)

`sembrar` imprime las claves una sola vez y no las guarda. Las cédulas son fijas:

| Cédula | Rol | Qué sirve para demostrar |
|---|---|---|
| 1750000018, 1750000026 | Afiliado (activo) | Reservar, confirmar, cancelar, reprogramar, apelar, delegar |
| 1750000513 | Representante | Operar para un titular solo con delegación vigente |
| 1750009019 / 1750009027 | Recepción (unidad Norte / Sur) | Agenda de su unidad; la otra unidad recibe denegación |
| 1750009035 | Profesional | Solo pacientes asignados |
| 1750009043 | Soporte | Ficha técnica enmascarada; nunca restablece claves |
| 1750009050 | Administración de oferta | Crear jornadas y cerrar franjas de su unidad |
| 1750009068 | Auditoría / DPD | Auditoría, derechos del titular, alertas, exportaciones con doble aprobación |
| 1750009076 | Administración de TI | Alertas de detección, segundo aprobador de exportaciones |

Los funcionarios configuran su segundo factor (TOTP) en el primer ingreso. Para obtener claves nuevas de otros afiliados sintéticos (1-50):
`docker compose exec app python manage.py usuarios_demo 3 4 5`.

## Qué demostrar en clase

1. **Seis demostraciones «antes y después»** contra una rama intencionalmente insegura (`lab/inseguro`, nunca se integra a `main`):
   `make lab` y luego `bash scripts/demos/demo_a.sh` … `demo_f.sh`. Guion en [`docs/demos.md`](docs/demos.md).
2. **Una sola orden verifica todo** lo prometido en el informe: `make verify` (o `bash scripts/verify.sh`). Añade carga, ZAP y restauración con
   `make verify-completo`. Deja evidencia con fecha y versión en [`docs/evidencia/`](docs/evidencia/).
3. **Trazabilidad automática** requisito → riesgo → control → prueba → módulo: [`docs/trazabilidad.md`](docs/trazabilidad.md).
4. **Detección y respuesta:** `/seguridad/alertas` (D01-D18), bloqueo de origen, desafío adaptativo y sala de espera.
5. **Auditoría inmutable:** `docker compose exec app python manage.py verificar_auditoria` y la demostración D.

## Comandos útiles

```sh
make verify                      # pytest + cobertura, check --deploy, Bandit, pip-audit, SBOM, gitleaks, cabeceras, auditoría, trazabilidad
docker compose --profile pruebas run --rm pruebas pytest        # solo las pruebas
docker compose --profile pruebas run --rm pruebas lint-imports  # contratos de arquitectura
bash scripts/respaldo.sh && bash scripts/restaurar.sh           # copia cifrada y restauración con RPO/RTO medidos
bash scripts/carga.sh                                           # k6: 200 req/s, ráfaga de 400, 8 000 usuarios
bash scripts/zap.sh                                             # OWASP ZAP baseline autenticado
```

## Documentación

| Documento | Contenido |
|---|---|
| [`docs/arquitectura.md`](docs/arquitectura.md) | Monolito modular por capas, zonas de confianza, flujos y decisiones |
| [`docs/seguridad.md`](docs/seguridad.md) | Controles por tipo y naturaleza y dónde vive cada uno en el código |
| [`docs/despliegue.md`](docs/despliegue.md) | Endurecimiento de contenedores y qué cambiaría en producción real |
| [`docs/demos.md`](docs/demos.md) | Las seis demostraciones antes/después |
| [`docs/flujo-git.md`](docs/flujo-git.md) | Rama protegida, revisión de cambios y lista de verificación del Anexo H |
| [`docs/limitaciones.md`](docs/limitaciones.md) | Limitaciones conocidas y lo que quedaría para producción |
| [`docs/revision_final.md`](docs/revision_final.md) | Revisión final: cumple / no cumple / simulado |
| [`docs/claves.md`](docs/claves.md) · [`docs/respuesta_incidentes.md`](docs/respuesta_incidentes.md) | Gestión de claves; plan de respuesta y simulacros |
| [`docs/endpoints.md`](docs/endpoints.md) · [`docs/trazabilidad.md`](docs/trazabilidad.md) | Inventario de rutas; matriz de trazabilidad (generados) |

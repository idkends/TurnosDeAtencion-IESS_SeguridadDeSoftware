# Portal de turnos IESS (prototipo académico)

Prototipo local, con datos 100 % sintéticos, de un portal de turnos de atención médica con seguridad de nivel
producción. Diseño completo: [`docs/informe.pdf`](docs/informe.pdf). Arquitectura: [`docs/arquitectura.md`](docs/arquitectura.md).
Reglas del proyecto: [`CLAUDE.md`](CLAUDE.md). **Estado: Etapa 1 de 4** (base, arquitectura, configuración segura, datos y autenticación).

## Arranque (requiere Docker)

```sh
python scripts/generar_env.py        # crea .env con secretos aleatorios (no se versiona)
sh docker/certs/generar.sh           # certificado TLS de desarrollo (mkcert si está instalado)
docker compose up -d --build         # proxy + app + PostgreSQL + Redis + afiliación simulada
docker compose --profile utilidades run --rm sembrar   # datos sintéticos (muestra las claves una sola vez)
```

Abrir <https://localhost:8443> (con el certificado autofirmado el navegador mostrará una advertencia; con `mkcert -install`, no).

Los mensajes "enviados" (códigos de activación, enlaces de recuperación) salen por la consola de la app:
`docker compose logs -f app`.

## Pruebas

```sh
docker compose --profile pruebas run --rm pruebas pytest          # todo
docker compose --profile pruebas run --rm pruebas lint-imports    # contratos de arquitectura
docker compose --profile pruebas run --rm pruebas python manage.py makemigrations --check --dry-run --skip-checks
```

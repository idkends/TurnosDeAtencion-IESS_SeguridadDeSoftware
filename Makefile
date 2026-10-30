# Atajos del proyecto. Todo corre en Docker (no hace falta Python 3.13 ni PostgreSQL locales).
# En Windows sin `make`, ejecute directamente los scripts:  bash scripts/verify.sh
.PHONY: arranque sembrar pruebas arquitectura verify verify-completo trazabilidad carga zap respaldo restaurar lab lab-bajar demos

arranque:            ## Crea .env y certificado, levanta todo y siembra datos sintéticos
	python scripts/generar_env.py
	sh docker/certs/generar.sh
	docker compose up -d --build
	docker compose --profile utilidades run --rm sembrar

pruebas:             ## Solo pytest
	docker compose --profile pruebas run --rm pruebas pytest

arquitectura:        ## Contratos de arquitectura
	docker compose --profile pruebas run --rm pruebas lint-imports

verify:              ## Verificación automática completa (pruebas, cobertura, check --deploy, Bandit, pip-audit, SBOM, gitleaks, cabeceras, auditoría, trazabilidad)
	bash scripts/verify.sh

verify-completo:     ## verify + carga k6 + ZAP + restauración de respaldo
	bash scripts/verify.sh --completo

trazabilidad:        ## Regenera docs/trazabilidad.md y avisa si algo queda sin prueba
	python scripts/trazabilidad.py

carga:               ## Prueba de carga k6 (T09)
	bash scripts/carga.sh

zap:                 ## Análisis dinámico OWASP ZAP (T14)
	bash scripts/zap.sh

respaldo:            ## Respaldo cifrado
	bash scripts/respaldo.sh

restaurar:           ## Restauración en entorno limpio con RPO/RTO
	bash scripts/restaurar.sh

lab:                 ## Levanta la rama insegura lab/inseguro (puerto 9443) para las demostraciones
	bash scripts/lab_levantar.sh

lab-bajar:           ## Elimina el entorno inseguro
	bash scripts/lab_bajar.sh

demos:               ## Las seis demostraciones antes/después (requiere `make lab`)
	for d in a b c d e f; do bash scripts/demos/demo_$$d.sh || exit 1; done

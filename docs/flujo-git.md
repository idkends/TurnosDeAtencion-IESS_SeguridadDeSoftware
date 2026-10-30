# Flujo de trabajo con Git (Anexo H)

## Reglas

- **Rama principal protegida (`main`):** solo se integra con una solicitud de cambio revisada y con `make verify` en verde. Una persona distinta del
  autor aprueba cada cambio. En GitHub/GitLab: *branch protection* con revisión obligatoria, comprobaciones de estado requeridas, historial lineal
  y sin *force-push*.
- **Commits que explican el control o el riesgo** que cubren, para alimentar la trazabilidad: «R15: autorización por objeto en turnos»,
  «R05: bloqueo progresivo de login». Commits pequeños.
- **Ramas de laboratorio inseguras (`lab/*`) viven aparte y nunca se integran a `main`.** `lab/inseguro` contiene las seis fallas intencionales
  de `docs/demos.md`; cada una está marcada con `⚠ INSEGURO A PROPÓSITO` en el código. Un cambio que vaya de `lab/*` a `main` debe rechazarse.
- Los scripts de demostración (`scripts/demos/`) sí están en `main`, porque atacan *ambas* ramas y es en `main` donde deben fallar.
- Ningún secreto en el repositorio: `.env` ignorado, `.env.example` sin valores; gitleaks revisa todo el historial (`make verify`, paso 7).

## Puertas por fase (resumen del Anexo H)

| Fase | Evidencia | Puerta |
|---|---|---|
| Requisitos y diseño | Informe, casos de abuso, evaluación de impacto | Cada requisito tiene riesgo, control y prueba (`docs/trazabilidad.md`, generado) |
| Desarrollo | Solicitudes de cambio revisadas | Aprobación de una persona distinta del autor |
| Integración continua | Informes de Bandit, pip-audit, SBOM, gitleaks | Sin hallazgos críticos ni altos abiertos |
| Pruebas | T01-T18 y CA-01..CA-16, ZAP, carga | Todas aprobadas |
| Entrega | `check --deploy`, cabeceras, restauración | Sin advertencias; RPO ≤ 5 min y RTO ≤ 30 min |

## Lista de verificación para la revisión de código (Anexo H) y cómo se comprueba

| # | Pregunta | Comprobación automática o manual |
|---|---|---|
| 1 | ¿Cada ruta nueva está en el inventario del Anexo D y comprueba autorización por objeto? | `tests/seguridad/test_inventario.py` falla si hay una ruta fuera de `config/inventario.py`; el 404 idéntico lo prueba `test_T13_*` |
| 2 | ¿Ninguna consulta se arma con texto del usuario (sin SQL crudo ni `**request.GET`)? | `grep` en la revisión final (ver `docs/revision_final.md`) y Bandit (B608) |
| 3 | ¿Los formularios declaran campos explícitos (sin `fields = '__all__'`)? | Los esquemas heredan de `FormularioEstricto` (campos desconocidos rechazados); `grep` en la revisión |
| 4 | ¿Ninguna plantilla marca contenido del usuario como seguro? | `grep` de `|safe`/`mark_safe`; única excepción justificada: el SVG del QR generado por `segno` |
| 5 | ¿Las operaciones que cambian estado usan POST, CSRF y, si corresponde, idempotencia? | `test_T14_R16_el_csrf_*`, `despachar(GET=…, POST=…)`, idempotencia en `reservar` |
| 6 | ¿Los mensajes de error y de login son genéricos? | `test_CA05_*`; demostración A |
| 7 | ¿Los registros omiten contraseñas, tokens y datos clínicos? | `test_T06_*` (formateador y flujos completos) |
| 8 | ¿Los secretos vienen de la configuración y no del código? | `test_ningun_secreto_literal_*`, gitleaks |
| 9 | ¿Cada regla de turnos tiene su restricción en la base y una prueba concurrente? | `apps/turnos/tests/test_restricciones_bd.py`, `tests/concurrencia/` (`test_T07_*`, `test_CA03_*`) |
| 10 | ¿La dependencia nueva tiene soporte vigente y no tiene CVE críticos? | pip-audit y SBOM en `make verify`; `requirements*.txt` con hashes |

Además, la **prueba de arquitectura** (`tests/arquitectura/`) hace fallar el cambio que rompa las reglas de capas: vistas finas, ORM de escritura solo
en `servicios.py`, permisos solo en `politicas.py`, escritura de auditoría solo en `auditoria`, terceros solo por adaptadores.

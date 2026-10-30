# Limitaciones conocidas y lo que quedaría para producción real

## Limitaciones del prototipo (honestas)

| # | Limitación | Efecto | Qué haría falta |
|---|---|---|---|
| 1 | **El truncado del final de la auditoría no se detecta con la cadena sola.** Una cadena de hashes detecta alterar o borrar un eslabón intermedio, pero si alguien con acceso total a la base borra los últimos registros, `verificar_auditoria` sigue en verde (se ve en la demo D, en el entorno inseguro) | En `main` ese acceso no existe para la aplicación (permisos + disparador), pero un DBA sí podría | Anclar periódicamente el último hash en un almacenamiento externo inmutable (copia externa, sellado de tiempo) |
| 2 | Una sola instancia de cada servicio; sin conmutación automática | Disponibilidad de un solo nodo | Nodos redundantes y réplica (ver `docs/despliegue.md`) |
| 3 | Prueba de carga con un solo anfitrión (k6 + aplicación + base en la misma máquina). La ráfaga de 400 req/s queda **justo** en la meta (p95 ≈ 1,9–2,0 s en la medición registrada) | El margen real depende del hardware; con nodos dedicados sería mayor | Repetir en un entorno dedicado; escalar procesos/nodos |
| 4 | La prueba de carga genera 8 000 «usuarios» con distinta IP mediante `X-Forwarded-For` hacia la aplicación (haciendo de proxy de confianza) porque desde un solo anfitrión no hay 8 000 IP reales | Mide la aplicación y sus límites por IP, no el proxy ni el WAF | Generadores de carga distribuidos |
| 5 | La caché de disponibilidad y de catálogos (10–60 s) puede mostrar una franja ya tomada | La reserva sigue siendo exacta: el servidor decide en la transacción | Invalidación por evento |
| 6 | ZAP *baseline* es pasivo + rastreo; el análisis autenticado usa una sesión de un afiliado sin segundo factor | No cubre ataques activos ni las rutas de funcionarios con TOTP | ZAP *full scan* con contexto de autenticación para cada rol, en un entorno desechable |
| 7 | La detección corre por sondeo cada 60 s dentro del mismo monolito | Latencia de hasta un minuto en las respuestas automáticas | Flujo de eventos y SIEM |
| 8 | El informe habla de «403» en la autorización por objeto; el diseño responde **404 idéntico** para turno ajeno e inexistente (CA-07) | Desviación documentada, más segura | — |
| 9 | Los servicios de afiliación y agenda, el correo y el SMS están **simulados** | No hay integración real | Interfaces oficiales con contrato |
| 10 | La clave de respaldo, las claves de auditoría y el cifrado de TOTP viven en variables de entorno | Aceptable en local | Gestor de secretos/KMS/HSM |
| 11 | Algunas constantes técnicas siguen en el código (HTTP 4xx/5xx de las vistas, longitudes de truncado de textos de auditoría, tamaño de lotes de las tareas, horarios de la siembra de datos); los parámetros de **seguridad y de turnos** sí están todos en `config/parametros.py` | Cambiar un lote o un truncado exige tocar código | Terminar de moverlos a la configuración; añadir una regla de arquitectura que lo vigile |
| 12 | Los controles físicos (F) y administrativos (A) son documentos, no código | — | Procedimientos reales y evidencias |
| 13 | El `Retry-After` de la sala de espera y de los límites es orientativo | — | — |

## Para producción real

Ver la tabla «Qué cambiaría en producción real» de [`docs/despliegue.md`](despliegue.md): WAF/CDN, nodos redundantes, gestor de secretos, SIEM, TLS
institucional, respaldos inmutables remotos, firma y escaneo continuo de imágenes, orquestador con políticas de red, y cumplimiento LOPDP completo
(delegado de protección de datos, evaluación de impacto, contratos con encargados).

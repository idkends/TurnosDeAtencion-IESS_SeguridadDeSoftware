# Plan de respuesta a incidentes y simulacros (R23, R20, T12)

Prototipo académico: los roles son los del portal y los plazos son los del informe (§3.3 y Anexo I). En producción los nombres de
persona se sustituyen por responsables reales y el plan lo aprueba el delegado de protección de datos (DPD).

## Responsables

| Rol | Quién | Qué hace |
|---|---|---|
| Responsable del incidente | `ADMIN_TI` de guardia | Declara el incidente, contiene (bloqueo de origen, revocación de sesiones, rotación de claves de `docs/claves.md`) |
| Delegado de protección de datos | `AUDITOR_DPD` | Evalúa si hay datos personales afectados, decide la notificación y la documenta |
| Soporte | `SOPORTE` | Atiende a las personas afectadas (verificación presencial; nunca restablece claves por sí mismo) |
| Dirección | Autoridad de la unidad | Aprueba comunicaciones externas |

## Detección → contención → notificación

1. **Detección:** el módulo `deteccion` (reglas D01-D18) abre una alerta con severidad en `/seguridad/alertas`; las críticas avisan de inmediato a `ADMIN_TI` y `AUDITOR_DPD`.
2. **Contención (≤ 1 h desde la alerta):** bloqueo del origen, desafío adaptativo, revocación de sesiones de las cuentas afectadas, rotación de la clave comprometida.
3. **Evaluación (≤ 24 h):** el DPD revisa la cadena de auditoría (`manage.py verificar_auditoria`) y determina el alcance.
4. **Notificación (≤ 72 h):** a la autoridad de protección de datos y a las personas afectadas, con el formato de abajo, si hay riesgo para sus derechos.
5. **Cierre:** lecciones aprendidas, corrección y, si corresponde, un asiento de corrección de auditoría con segundo supervisor.

### Formato de notificación a la persona afectada

Qué pasó (sin detalles técnicos explotables) · qué datos están afectados · cuándo se detectó · qué hicimos · qué debe hacer la persona ·
contacto presencial. Nunca se pide la clave, un código ni un enlace por mensaje (R20): el mensaje solo invita a acudir a la unidad o a
entrar escribiendo la dirección oficial.

## Simulacros

| Simulacro | Frecuencia | Qué se mide | Criterio |
|---|---|---|---|
| Incidente de acceso indebido (ejercicio de mesa) | Semestral | Tiempo de detección, de contención y de notificación | Dentro de los plazos de arriba |
| Phishing al personal (enlace falso de recuperación) | Semestral | Personas que ingresan datos; reporte al equipo de seguridad | Las pruebas T12/CA-16 demuestran que ningún enlace usa un dominio distinto del configurado |
| Restauración de respaldo | Trimestral | RPO y RTO (`scripts/restaurar.sh`, evidencia en `docs/evidencia/`) | RPO ≤ 5 min, RTO ≤ 30 min |

Última evidencia automática: ver `docs/evidencia/` (la regla D15 alerta si pasan 90 días sin prueba de restauración).

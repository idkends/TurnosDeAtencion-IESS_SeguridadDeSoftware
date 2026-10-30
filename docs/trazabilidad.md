# Matriz de trazabilidad (generada)

> Generado por `scripts/trazabilidad.py` el 2026-10-30 16:36 UTC a partir de los nombres de las pruebas. No editar a mano.
> Los vínculos vienen del Anexo C, §10.1, §10.2 y Anexo G del informe (`scripts/trazabilidad_datos.py`).

## Resumen

- Pruebas T del §10.1 con función de prueba: **19/19**
- Casos de abuso CA-01..CA-16 con función de prueba: **16/16**
- Riesgos R01..R23 con prueba: **23/23**
- Requisitos I01..I14 con prueba: **14/14**
- Módulos M01..M14 con prueba: **14/14**
- Funciones de prueba nombradas con identificador: **97**

## Del requisito a la prueba

| Requisito | Decisión | Riesgos | Control principal | Pruebas | Módulo |
|---|---|---|---|---|---|
| I01 | Conservar | R12 R13 R14 R15 R21 | RT-01 a RT-12; restricciones únicas; idempotencia | T07 T09 T13 T16 T17 | M04 |
| I02 | Modificar | R18 | Datos mínimos; la fecha de nacimiento no autentica | T03 | M01 |
| I03 | Eliminar | R01 | No se recolecta | T01 | M04 |
| I04 | Eliminar | R02 | Agenda separada de lo clínico | T01 | M04 |
| I05 | Eliminar | R01 | No se recolecta | T01 | M04 |
| I06 | Modificar | R04 R15 | Delegación con alcance y vigencia | T02 T13 | M05 |
| I07 | Modificar | R05 R18 R19 R20 | Activación, Argon2id, segundo factor, bloqueo | T03 T12 | M01 M02 |
| I08 | Modificar | R06 | RBAC, relación asistencial, autorización por objeto | T04 | M03 M14 |
| I09 | Modificar | R03 R20 | Mensajes neutrales; tokens de un solo uso | T05 T12 | M06 |
| I10 | Conservar | R07 | Autenticación entre servicios; PENDIENTE; circuito abierto | T07 | M08 |
| I11 | Modificar | R08 R22 | Solo adición; hash encadenado; sin secretos | T06 T11b | M07 M14 |
| I12 | Modificar | R09 R21 | Separación, redundancia, respaldos | T08 T09 | M13 |
| I13 | Modificar | R10 | Despliegue gradual; SLO | T08 | M13 |
| I14 | Modificar | R11 | Plazos; sin entrega para IA; contratos | T10 T11 | M12 |

## Del riesgo al control, la prueba y el módulo

| Riesgo | Control preventivo | Pruebas T | Casos de abuso | Módulo | Código que lo prueba |
|---|---|---|---|---|---|
| R01 | No recolectar: esquema sin esos campos | T01 | — | M04 M12 | tests/seguridad |
| R02 | Separar agenda y sistema clínico | T01 | — | M04 | tests/seguridad |
| R03 | Mensajes neutrales; enlace de un solo uso que exige sesión | T05 | CA06 | M06 | cuentas, notificaciones, tests/seguridad |
| R04 | Delegación con alcance, vigencia y revocación | T02 | CA01 | M05 | cuentas, delegaciones, tests/seguridad, turnos |
| R05 | Activación de un solo uso; Argon2id; bloqueo; segundo factor | T03 | CA04 | M01 M02 | cuentas |
| R06 | RBAC + relación asistencial; denegar por defecto | T04 | CA09 | M03 M14 | cuentas, tests/seguridad, turnos |
| R07 | Autenticación entre servicios, tiempos de espera, circuito abierto | T07 | CA15 | M08 | integraciones, tests/concurrencia, turnos |
| R08 | Lista blanca de campos y redacción de secretos | T06 | — | M07 | notificaciones, tests/seguridad |
| R09 | Separación, redundancia y respaldos | T08 | — | M13 | tests/seguridad |
| R10 | Despliegues graduales sin interrupción | T08 | — | M13 | tests/seguridad |
| R11 | Sin entrega para IA; plazos de conservación | T10 T11 | — | M12 | derechos, tests/seguridad |
| R12 | RT-01..RT-04, RT-08, RT-09; anti-automatización | T16 | CA02 CA12 | M04 M09 | delegaciones, tests/concurrencia, tests/seguridad, turnos |
| R13 | Índice único parcial, bloqueo de fila, idempotencia | T07 | CA03 CA13 | M04 | integraciones, tests/concurrencia, tests/seguridad, turnos |
| R14 | Confirmación, liberación, bloqueo proporcional y apelación | T17 | CA14 | M04 | turnos |
| R15 | Autorización por objeto e identificadores no secuenciales | T13 | CA07 | M03 | cuentas, tests/seguridad |
| R16 | ORM parametrizado, autoescape, CSRF, CSP | T14 | CA08 | M10 | tests/seguridad |
| R17 | Versiones LTS, pip-audit, SBOM, parcheo | T15 | CA16 | M11 | cuentas, tests/seguridad |
| R18 | Mensajes genéricos, tiempos constantes, límites por origen | T03 | CA05 | M01 M09 | cuentas |
| R19 | Sesión 24 h / 1 h de inactividad, cookies seguras, revocación | T03 | CA06 CA11 | M02 | cuentas, tests/seguridad |
| R20 | Dominios oficiales, no pedir claves, segundo factor | T12 | CA04 CA16 | M01 M02 | cuentas, deteccion, tests/seguridad |
| R21 | Límites de solicitudes, caché, sala de espera | T09 | CA12 | M09 | tests/seguridad |
| R22 | Solo adición, hash encadenado, copia externa | T11b | CA10 | M07 | auditoria, tests/seguridad |
| R23 | Plan de respuesta, responsables, notificación | T12 | — | M07 M14 | deteccion, tests/seguridad |

## Pruebas T01 a T18

| ID | Qué verifica | Riesgos | Funciones de prueba |
|---|---|---|---|
| T01 | Reservar sin datos laborales/religiosos/de salud; esquema sin esos campos | R01 R02 | 2: `tests/seguridad/test_verificacion_informe.py::test_T01_el_esquema_no_tiene_campos_laborales_religiosos_ni_de_salud`<br>`tests/seguridad/test_verificacion_informe.py::test_T01_la_reserva_se_completa_sin_ningun_dato_laboral_religioso_ni_de_salud` |
| T02 | Cédula ajena sin delegación, delegación revocada y válida | R04 | 1: `apps/delegaciones/tests/test_delegaciones.py::test_T02_crear_una_delegacion_exige_segundo_factor_reciente` |
| T03 | Clave biográfica, activación reutilizada, bloqueo, cédula inexistente, expiración y revocación de sesión | R05 R18 R19 | 6: `apps/cuentas/tests/test_activacion.py::test_T03_activacion_no_reutilizable`<br>`apps/cuentas/tests/test_activacion.py::test_T03_clave_biografica_rechazada_y_el_codigo_no_se_consume`<br>`apps/cuentas/tests/test_claves.py::test_T03_clave_basada_en_la_fecha_de_nacimiento_rechazada`<br>`apps/cuentas/tests/test_mfa.py::test_T03_sesion_con_mas_de_1_hora_de_inactividad_no_hace_operaciones_sensibles`<br>… (+2) |
| T04 | Matriz de permisos por rol; denegaciones auditadas | R06 | 2: `tests/seguridad/test_matriz_roles.py::test_T04_matriz_de_permisos`<br>`tests/seguridad/test_paneles_etapa3.py::test_T04_matriz_de_las_rutas_de_la_etapa_3` |
| T05 | Notificación neutral; enlace sin datos que pide autenticación | R03 | 3: `apps/notificaciones/tests/test_notificaciones.py::test_T05_todas_las_plantillas_son_neutrales`<br>`apps/notificaciones/tests/test_notificaciones.py::test_T05_solo_los_enlaces_de_un_solo_uso_pueden_llevar_url`<br>`tests/seguridad/test_endpoints.py::test_T05_E20_el_enlace_redirige_a_iniciar_sesion_sirve_una_vez_y_no_abre_datos` |
| T06 | Contraseñas, tokens y diagnóstico no aparecen en los registros | R08 | 5: `apps/notificaciones/tests/test_notificaciones.py::test_T06_el_envio_se_registra_sin_cuerpo_ni_secretos`<br>`tests/seguridad/test_endpoints.py::test_T06_R08_los_registros_de_acceso_del_proxy_no_llevan_cedulas_ni_tokens_ni_consultas`<br>`tests/seguridad/test_logs_y_claves.py::test_T06_el_formateador_redacta_cada_marcador_sensible`<br>`tests/seguridad/test_logs_y_claves.py::test_T06_el_formateador_redacta_tambien_la_traza_de_las_excepciones`<br>… (+1) |
| T07 | Concurrencia por la última franja; idempotencia; servicio externo lento | R07 R13 | 2: `apps/integraciones/tests/test_adaptador_http.py::test_T07_RT10_error_5xx_es_servicio_no_disponible_nunca_cobertura`<br>`tests/concurrencia/test_reservas_concurrentes.py::test_T07_mil_intentos_por_la_ultima_franja_una_sola_venta` |
| T08 | Restaurar un respaldo con RPO/RTO medidos; despliegue sin perder reservas | R09 R10 | 2: `tests/seguridad/test_respaldos.py::test_T08_el_compose_archiva_wal_y_guarda_copias_fuera_del_volumen_de_la_base`<br>`tests/seguridad/test_respaldos.py::test_T08_la_ultima_prueba_de_restauracion_cumple_rpo_y_rto` |
| T09 | Carga de 200 req/s con ráfaga de 400; el límite no bloquea a todos | R21 | 2: `tests/seguridad/test_verificacion_informe.py::test_T09_la_ultima_prueba_de_carga_cumple_las_metas_del_informe`<br>`tests/seguridad/test_verificacion_informe.py::test_T09_el_contador_atomico_de_redis_cuenta_y_caduca` |
| T10 | Integraciones mínimas y revocables; sin exportación para entrenar IA | R11 | 3: `tests/seguridad/test_verificacion_informe.py::test_T10_la_respuesta_de_afiliacion_es_minima_sin_datos_de_salud_ni_laborales`<br>`tests/seguridad/test_verificacion_informe.py::test_T10_no_existe_ninguna_ruta_de_exportacion_masiva_ni_para_entrenar_ia`<br>`tests/seguridad/test_verificacion_informe.py::test_T10_el_acceso_del_tercero_es_revocable_cambiando_su_clave_de_servicio` |
| T11 | Vencimiento de datos, acceso y eliminación | R01 R11 | 6: `apps/derechos/tests/test_derechos.py::test_T11_las_cuentas_inactivas_24_meses_se_anonimizan_con_constancia`<br>`apps/derechos/tests/test_derechos.py::test_T11_los_turnos_de_mas_de_12_meses_pasan_a_agregado_anonimo_y_los_recientes_no`<br>`apps/derechos/tests/test_derechos.py::test_T11_los_turnos_activos_nunca_se_tocan_aunque_sean_antiguos`<br>`apps/derechos/tests/test_derechos.py::test_T11_notificaciones_6_meses_y_credenciales_vencidas_se_eliminan`<br>… (+2) |
| T11b | Auditoría de solo adición: alteración detectada | R22 | 2: `apps/auditoria/tests/test_cadena.py::test_T11b_una_fila_alterada_a_mano_la_detecta_la_verificacion`<br>`apps/auditoria/tests/test_cadena.py::test_T11b_una_fila_borrada_en_medio_rompe_la_cadena` |
| T12 | Simulacro de incidente y suplantación (enlaces, dominios, muchas cédulas desde una IP) | R20 R23 | 3: `apps/deteccion/tests/test_reglas.py::test_T12_D04_enumeracion_de_30_cedulas_desde_una_ip_bloquea_el_origen`<br>`tests/seguridad/test_verificacion_informe.py::test_T12_el_plan_de_respuesta_define_responsables_plazos_formato_y_simulacros`<br>`tests/seguridad/test_verificacion_informe.py::test_T12_ningun_mensaje_pide_la_clave_ni_un_codigo_por_correo_o_sms` |
| T13 | Cambiar el identificador de un turno o afiliado: 403/404 y registro | R15 | 2: `tests/seguridad/test_endpoints.py::test_T13_IDOR_en_las_acciones_sobre_un_turno_ajeno_da_404_y_no_cambia_nada`<br>`tests/seguridad/test_endpoints.py::test_T13_IDOR_el_representante_sin_delegacion_tampoco_actua_sobre_turnos_ajenos` |
| T14 | Inyección SQL, XSS y CSRF; análisis estático | R16 | 3: `tests/seguridad/test_configuracion.py::test_T14_R16_el_csrf_esta_activo_y_rechaza_posts_sin_token`<br>`tests/seguridad/test_verificacion_informe.py::test_T14_el_ultimo_analisis_de_ZAP_no_tiene_hallazgos_altos_ni_medios_sin_justificar`<br>`tests/seguridad/test_verificacion_informe.py::test_T14_cada_IGNORE_de_ZAP_lleva_justificacion` |
| T15 | Dependencias, SBOM y versión de Django | R17 | 3: `tests/seguridad/test_verificacion_informe.py::test_T15_django_esta_en_la_linea_5_2_LTS_y_todo_esta_fijado_con_hashes`<br>`tests/seguridad/test_verificacion_informe.py::test_T15_la_ultima_verificacion_de_dependencias_no_tiene_criticos_ni_altos`<br>`tests/seguridad/test_verificacion_informe.py::test_T15_existe_un_SBOM_CycloneDX_valido_de_la_ultima_verificacion` |
| T16 | Reglas de turnos: segunda reserva, tope, solape, seis representados, muchas cédulas | R12 | 4: `apps/delegaciones/tests/test_delegaciones.py::test_T16_el_limite_de_5_representados_cuenta_solo_las_delegaciones_vigentes`<br>`apps/turnos/tests/test_reserva.py::test_T16_tope_de_3_citas_activas_en_total`<br>`apps/turnos/tests/test_reserva.py::test_T16_sin_solape_de_horario_para_la_misma_persona`<br>`tests/concurrencia/test_reservas_concurrentes.py::test_T16_RT01_la_misma_persona_en_20_franjas_a_la_vez_obtiene_un_solo_turno_por_especialidad` |
| T17 | Confirmación/expiración, inasistencias, justificación, apelación, prioritarios | R14 | 1: `apps/turnos/tests/test_inasistencias.py::test_T17_justificar_dentro_de_72_horas_revierte_la_inasistencia_y_la_restriccion` |
| T18 | check --deploy, cabeceras, cookies, TLS, DEBUG y secretos | R09 | 6: `tests/seguridad/test_configuracion.py::test_T18_check_deploy_en_produccion_no_da_advertencias`<br>`tests/seguridad/test_configuracion.py::test_T18_check_deploy_falla_si_se_debilita_la_configuracion`<br>`tests/seguridad/test_repositorio.py::test_T18_los_servicios_estan_endurecidos_sin_root_solo_lectura_sin_capacidades_y_con_limites`<br>`tests/seguridad/test_repositorio.py::test_T18_las_imagenes_tienen_version_y_resumen_fijados`<br>… (+2) |

## Casos de abuso CA-01 a CA-16

| ID | Caso | Riesgos | Funciones de prueba |
|---|---|---|---|
| CA-01 | Reservar el turno de otra persona con su cédula | R04 | 3: `apps/cuentas/tests/test_politicas.py::test_CA01_el_representante_solo_ve_el_turno_con_delegacion_vigente`<br>`apps/turnos/tests/test_reserva.py::test_CA01_sin_delegacion_no_se_reserva_a_nombre_de_otra_persona`<br>`tests/seguridad/test_endpoints.py::test_CA01_representante_sin_delegacion_recibe_403_y_no_se_crea_turno` |
| CA-02 | Acaparar cupos en la misma especialidad | R12 | 2: `apps/turnos/tests/test_reserva.py::test_CA02_segunda_reserva_en_la_misma_especialidad_se_rechaza_con_mensaje_generico`<br>`tests/seguridad/test_endpoints.py::test_CA02_segunda_reserva_en_la_especialidad_responde_409_con_mensaje_generico` |
| CA-03 | Ganar la última franja con solicitudes simultáneas | R13 | 1: `tests/concurrencia/test_reservas_concurrentes.py::test_CA03_dos_solicitudes_simultaneas_por_la_ultima_franja_solo_una_crea_turno` |
| CA-04 | Activar la cuenta de otra persona con mi correo (pre-secuestro) | R05 R20 | 1: `apps/cuentas/tests/test_activacion.py::test_CA04_no_hay_forma_de_indicar_otro_contacto` |
| CA-05 | Averiguar qué cédulas son afiliadas | R18 | 6: `apps/cuentas/tests/test_activacion.py::test_CA05_cedula_inexistente_indistinguible_de_una_existente`<br>`apps/cuentas/tests/test_activacion.py::test_CA05_el_tiempo_de_respuesta_no_delata_si_la_cedula_existe`<br>`apps/cuentas/tests/test_login.py::test_CA05_cedula_inexistente_y_clave_incorrecta_dan_el_mismo_mensaje_y_estado`<br>… (+3) |
| CA-06 | Reutilizar un enlace de restablecimiento o de aviso | R03 R19 | 3: `apps/cuentas/tests/test_recuperacion.py::test_CA06_el_token_sirve_una_sola_vez`<br>`apps/cuentas/tests/test_recuperacion.py::test_CA06_el_token_vence_a_los_15_minutos`<br>`apps/cuentas/tests/test_recuperacion.py::test_CA06_dos_solicitudes_simultaneas_con_el_mismo_token_solo_una_gana` |
| CA-07 | Leer el turno de otra persona cambiando el identificador | R15 | 3: `apps/cuentas/tests/test_politicas.py::test_CA07_otro_afiliado_no_ve_el_turno_ajeno`<br>`apps/cuentas/tests/test_sesiones.py::test_CA07_no_se_puede_revocar_la_sesion_de_otra_persona_y_la_respuesta_no_distingue`<br>`tests/seguridad/test_endpoints.py::test_CA07_pedir_el_turno_de_otro_da_la_misma_respuesta_que_un_turno_inexistente` |
| CA-08 | Inyectar SQL o scripts en login, búsqueda y apelaciones | R16 | 2: `tests/seguridad/test_configuracion.py::test_CA08_T14_inyeccion_en_login_falla_sin_error_tecnico_visible`<br>`tests/seguridad/test_endpoints.py::test_CA08_E12_apelar_por_http_con_texto_escapado` |
| CA-09 | Recepción que ve pacientes de otra unidad | R06 | 3: `apps/cuentas/tests/test_politicas.py::test_CA09_recepcion_solo_ve_turnos_de_su_unidad`<br>`apps/turnos/tests/test_inasistencias.py::test_CA09_recepcion_de_otra_unidad_recibe_denegacion_y_queda_auditado`<br>`tests/seguridad/test_matriz_roles.py::test_CA09_recepcion_de_otra_unidad_no_ve_la_agenda_ni_los_datos_de_la_unidad_ajena` |
| CA-10 | Supervisor que edita un registro de auditoría | R22 | 2: `apps/auditoria/tests/test_cadena.py::test_CA10_la_base_rechaza_update_delete_y_truncate_incluso_al_dueno`<br>`tests/seguridad/test_paneles_etapa3.py::test_CA10_un_supervisor_no_puede_editar_un_registro_de_auditoria` |
| CA-11 | Usar una cookie robada pasadas 24 horas | R19 | 1: `tests/seguridad/test_paneles_etapa3.py::test_CA11_una_cookie_de_sesion_pasadas_24_horas_o_revocada_no_sirve` |
| CA-12 | Automatizar reservas con un bot | R12 R21 | 1: `tests/seguridad/test_paneles_etapa3.py::test_CA12_cien_reservas_por_minuto_desde_una_ip_se_limitan_sin_bloquear_a_los_legitimos` |
| CA-13 | Doble clic o reintento de una reserva | R13 | 3: `apps/turnos/tests/test_reserva.py::test_CA13_el_reintento_con_la_misma_clave_devuelve_el_mismo_turno`<br>`tests/concurrencia/test_reservas_concurrentes.py::test_CA13_20_solicitudes_simultaneas_con_la_misma_clave_crean_un_solo_turno`<br>`tests/seguridad/test_endpoints.py::test_CA13_doble_clic_por_http_la_segunda_respuesta_es_igual_a_la_primera` |
| CA-14 | Afiliado bloqueado sin poder reclamar | R14 | 1: `apps/turnos/tests/test_inasistencias.py::test_CA14_dos_inasistencias_consecutivas_bloquean_la_reserva_en_linea_14_dias_con_causa_y_fecha` |
| CA-15 | Servicio de afiliación falla y se confirma cobertura falsa | R07 | 4: `apps/integraciones/tests/test_adaptador_http.py::test_CA15_tiempo_de_espera_agotado_es_servicio_no_disponible`<br>`apps/integraciones/tests/test_adaptador_http.py::test_CA15_circuito_abierto_tras_5_fallos_y_deja_de_llamar_al_tercero`<br>`apps/integraciones/tests/test_agenda.py::test_CA15_tiempo_de_espera_agotado_es_servicio_no_disponible`<br>… (+1) |
| CA-16 | Envenenar un enlace de restablecimiento con la cabecera Host | R17 R20 | 3: `apps/cuentas/tests/test_recuperacion.py::test_CA16_un_Host_falso_se_rechaza_y_no_genera_enlaces`<br>`apps/cuentas/tests/test_recuperacion.py::test_CA16_aun_con_un_host_permitido_el_enlace_usa_el_dominio_de_la_configuracion`<br>`tests/seguridad/test_paneles_etapa3.py::test_CA16_un_Host_falso_no_envenena_ningun_enlace_ni_siquiera_el_del_soporte` |

## Módulos M01-M14

| Módulo | Nombre | Riesgos que cubre | Con prueba |
|---|---|---|---|
| M01 | Cuentas y activación | R05 R18 R20 | sí |
| M02 | Sesiones y segundo factor | R05 R19 R20 | sí |
| M03 | Autorización | R06 R15 | sí |
| M04 | Turnos y reglas | R01 R02 R12 R13 R14 | sí |
| M05 | Delegaciones | R04 | sí |
| M06 | Notificaciones neutrales | R03 | sí |
| M07 | Auditoría de solo adición | R08 R22 R23 | sí |
| M08 | Integraciones simuladas | R07 | sí |
| M09 | Límites y antiautomatización | R12 R18 R21 | sí |
| M10 | Cabeceras, CSRF y configuración | R16 | sí |
| M11 | Dependencias e integración continua | R17 | sí |
| M12 | Retención y derechos | R01 R11 | sí |
| M13 | Respaldos y restauración | R09 R10 | sí |
| M14 | Paneles de auditor y DPD | R06 R23 | sí |

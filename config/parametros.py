"""Parámetros de seguridad y de turnos del sistema (Anexo E del informe).

Es el ÚNICO lugar donde viven estos números. El resto del código importa `P` y nunca escribe un valor
literal. Cada parámetro puede sobrescribirse con una variable de entorno `PARAM_<NOMBRE_EN_MAYUSCULAS>`;
los valores por defecto son los propuestos en el informe (la institución debe aprobarlos).
"""
import os
from dataclasses import dataclass, fields


def _desde_entorno(nombre: str, defecto):
    crudo = os.environ.get(f"PARAM_{nombre.upper()}")
    if crudo is None or crudo == "":
        return defecto
    if isinstance(defecto, bool):
        return crudo.strip().lower() in {"1", "true", "si", "sí", "yes"}
    if isinstance(defecto, int):
        return int(crudo)
    if isinstance(defecto, float):
        return float(crudo)
    if isinstance(defecto, tuple):
        return tuple(int(x) for x in crudo.split(","))
    return crudo


@dataclass(frozen=True)
class Parametros:
    # --- Claves (Anexo E: "Longitud mínima de la clave", "Almacenamiento") ---
    clave_longitud_minima: int = 12
    clave_longitud_maxima: int = 128  # evita usar Argon2 como vector de agotamiento de recursos

    # --- Activación de cuenta (8 dígitos, 15 min, un solo uso, 5 intentos) ---
    activacion_digitos: int = 8
    activacion_minutos: int = 15
    activacion_intentos: int = 5

    # --- Inicio de sesión: 5 fallos seguidos -> espera de 1, 5, 15 y 60 min; 10 intentos/min/IP ---
    login_fallos_por_escalon: int = 5
    login_esperas_minutos: tuple = (1, 5, 15, 60)
    login_ventana_estado_horas: int = 24  # tras este tiempo sin fallos se olvida el contador
    login_intentos_por_minuto_ip: int = 10

    # --- Segundo factor: TOTP de 30 s con tolerancia de un paso; 10 códigos de recuperación ---
    totp_periodo_segundos: int = 30
    totp_tolerancia_pasos: int = 1
    totp_digitos: int = 6
    totp_codigos_recuperacion: int = 10
    totp_intentos_por_minuto_ip: int = 10
    mfa_reciente_minutos: int = 10  # antigüedad máxima del segundo factor en una operación sensible
    preautenticacion_minutos: int = 5  # tiempo para completar el segundo factor tras la clave

    # --- Sesión: 24 h en total, 1 h de inactividad en operaciones sensibles, hasta 3 dispositivos ---
    sesion_horas_totales: int = 24
    sesion_inactividad_sensible_minutos: int = 60
    sesion_dispositivos_maximos: int = 3
    sesion_actualizar_actividad_segundos: int = 60  # evita una escritura por cada solicitud
    dispositivo_cookie_dias: int = 365

    # --- Recuperación de clave (Anexo F): token de 128 bits, 15 min, un solo uso ---
    recuperacion_bits_token: int = 128
    recuperacion_minutos: int = 15
    recuperacion_solicitudes_por_hora_cedula: int = 5
    recuperacion_solicitudes_por_hora_ip: int = 20
    activacion_solicitudes_por_hora_cedula: int = 5
    activacion_solicitudes_por_hora_ip: int = 20

    # --- Igualdad de tiempos de respuesta en flujos de identidad (CWE-204) ---
    tiempo_minimo_respuesta_auth_ms: int = 350

    # --- Turnos (RT-01 a RT-09) ---
    turnos_dias_anticipacion: int = 30
    turnos_horas_para_confirmar: int = 24
    turnos_horas_minimas_para_cancelar: int = 2
    turnos_reprogramaciones_maximas: int = 2
    turnos_activos_por_especialidad: int = 1
    turnos_activos_totales: int = 3
    catalogos_cache_segundos: int = 60
    # --- Detección y respuesta (Anexo I): ventanas y duraciones que antes estaban en el código de las reglas ---
    d06_ventana_ip_minutos: int = 15
    d06_bloqueo_origen_segundos: int = 600
    d10_revision_dias: int = 7
    d14_ventana_minutos: int = 10
    d15_historial_respaldos_dias: int = 365
    d16_ventana_dias: int = 7
    # --- Exportaciones puntuales, listados y conservación ---
    exportacion_dias_maximos: int = 31
    exportacion_motivo_minimo: int = 10
    delegaciones_listado_maximo: int = 50
    enlaces_vencidos_conservacion_dias: int = 7
    dias_por_mes: int = 30   # convención de retención: un «mes» de conservación son 30 días
    disponibilidad_fragmento_cache_segundos: int = 10
    turnos_representados_por_representante: int = 5
    turnos_porcentaje_cupos_prioritarios: int = 10

    # --- Política de inasistencias (sección 3.5) ---
    inasistencias_ventana_dias: int = 90
    inasistencias_tope_reducido: int = 1
    inasistencias_consecutivas_para_bloqueo: int = 2
    inasistencias_bloqueo_dias: int = 14
    inasistencias_horas_para_justificar: int = 72
    inasistencias_dias_habiles_apelacion: int = 5

    turnos_pendiente_horas_maximas: int = 2  # un turno PENDIENTE sin confirmar por la agenda libera su cupo
    turnos_deshacer_cancelacion_minutos: int = 10
    inasistencias_revision_humana: int = 3  # tres inasistencias en la ventana: revisión humana, sin sanción automática
    apelacion_texto_maximo: int = 500

    # --- Consultas de funcionarios, derechos y enlaces ---
    consultas_contacto_por_hora_funcionario: int = 30
    justificacion_acceso_minimo_caracteres: int = 10
    derechos_plazo_requerimiento_dias: int = 10
    derechos_plazo_atencion_dias: int = 15
    enlace_token_horas: int = 24
    paginacion_por_defecto: int = 20
    paginacion_maxima: int = 100

    # --- Delegaciones (sección 4.4) ---
    delegacion_vigencia_meses: int = 12

    # --- Límites de solicitudes ---
    limite_disponibilidad_por_minuto: int = 60
    limite_reservas_por_hora_cuenta: int = 10
    limite_api_por_minuto_cuenta: int = 120

    # --- Tamaños y tiempos ---
    cuerpo_maximo_bytes: int = 64 * 1024
    tiempo_solicitud_segundos: int = 10

    # --- Integraciones (adaptadores) ---
    integracion_timeout_segundos: float = 3.0
    integracion_reintentos: int = 2
    integracion_espera_base_segundos: float = 0.2
    circuito_fallos: int = 5
    circuito_ventana_segundos: int = 30
    circuito_pausa_segundos: int = 30
    afiliacion_cache_minutos: int = 15
    servicio_token_tolerancia_segundos: int = 60

    # --- Auditoría ---
    auditoria_meses_en_linea: int = 12
    auditoria_detalle_maximo_caracteres: int = 500

    # --- Notificaciones ---
    notificaciones_conservacion_meses: int = 6
    recordatorio_horas: tuple = (72, 24)  # recordatorios neutrales antes de la cita
    consentimiento_cambio_contacto_horas_espera: int = 24  # espera para operaciones sensibles tras cambiar el contacto (Anexo F)
    cambio_contacto_minutos: int = 15

    # --- Detección y respuesta (Anexo I): umbrales parametrizables ---
    d01_fallos_por_ip: int = 50
    d01_ventana_minutos: int = 10
    d02_cuentas_distintas: int = 200
    d02_ventana_minutos: int = 10
    d03_fallos_por_cuenta: int = 10
    d03_ips_distintas: int = 5
    d03_ventana_minutos: int = 10
    d04_cedulas_distintas: int = 30
    d04_ventana_minutos: int = 10
    d06_intentos_por_hora_cuenta: int = 10
    d06_cuentas_distintas_por_ip: int = 5
    d07_respuestas_por_usuario: int = 20
    d07_ventana_minutos: int = 5
    d08_fichas_por_hora: int = 100
    horario_laboral_inicio: int = 7
    horario_laboral_fin: int = 19
    d12_porcentaje_5xx: float = 2.0
    d12_muestra_minima: int = 50
    d13_p95_segundos: float = 2.0
    d13_ventana_minutos: int = 5
    d14_fallos_terceros: int = 5
    d14_ventana_segundos: int = 30
    d15_dias_sin_prueba_restauracion: int = 90
    d16_plazo_horas: int = 72
    bloqueo_origen_minutos: int = 30
    desafio_minutos: int = 30
    sala_espera_minutos: int = 10
    sala_espera_admision_por_segundo: int = 20
    metricas_ventana_segundos: int = 300

    # --- Acceso excepcional, exportaciones y retención de datos (sección 2.2) ---
    emergencia_motivo_minimo: int = 20
    exportacion_filas_maximas: int = 1000
    exportacion_vigencia_horas: int = 24
    retencion_cuenta_inactiva_meses: int = 24
    retencion_turnos_meses: int = 12
    retencion_restricciones_meses: int = 12
    retencion_delegaciones_meses: int = 12
    retencion_codigos_dias: int = 7
    encargado_plazo_notificacion_dias: int = 3
    encargado_plazo_eliminacion_dias: int = 5

    def __post_init__(self):
        for campo in fields(self):
            valor = _desde_entorno(campo.name, getattr(self, campo.name))
            object.__setattr__(self, campo.name, valor)


P = Parametros()

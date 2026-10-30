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
    turnos_representados_por_representante: int = 5
    turnos_porcentaje_cupos_prioritarios: int = 10

    # --- Política de inasistencias (sección 3.5) ---
    inasistencias_ventana_dias: int = 90
    inasistencias_tope_reducido: int = 1
    inasistencias_consecutivas_para_bloqueo: int = 2
    inasistencias_bloqueo_dias: int = 14
    inasistencias_horas_para_justificar: int = 72
    inasistencias_dias_habiles_apelacion: int = 5

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

    def __post_init__(self):
        for campo in fields(self):
            valor = _desde_entorno(campo.name, getattr(self, campo.name))
            object.__setattr__(self, campo.name, valor)


P = Parametros()

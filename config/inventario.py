"""Inventario de endpoints (Anexo D del informe): la FUENTE de lo que el portal publica.

Una ruta que no está aquí no se publica: `tests/seguridad/test_inventario.py` falla si existe una ruta sin entrada (o una
entrada sin ruta) y si `docs/endpoints.md` no coincide con este archivo (se regenera con `manage.py generar_inventario`).

E01-E21 son los endpoints del Anexo D. Las S (S01, S02...) son RUTAS DE SOPORTE que el Anexo D no numera pero que el prototipo
necesita (pantallas de formularios y los pasos del Anexo F): se documentan igual y pasan por las mismas pruebas.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Entrada:
    id: str
    metodos: tuple  # métodos HTTP aceptados por el servidor
    ruta: str  # con marcadores {id}, {cedula}, {token}
    funcion: str
    autenticacion: str  # anonimo | sesion | sesion_mfa_reciente | funcionario | auditor | interna | credenciales | token
    autorizacion: str
    limites_texto: str
    idempotencia_csrf: str
    riesgos: str
    # (ámbito, "ip"|"cuenta", nombre del parámetro en config.parametros.P, ventana en segundos)
    limites: tuple = ()
    soporte: bool = False

    @property
    def requiere_sesion(self) -> bool:
        """Las rutas anónimas, de credenciales, de token o de red interna no exigen sesión completa."""
        return not self.autenticacion.startswith(("anonimo", "credenciales", "token", "solo red interna", "sesion parcial", "sesion o sesion parcial"))


_API = ("api", "cuenta", "limite_api_por_minuto_cuenta", 60)
_DISP_IP = ("disponibilidad-ip", "ip", "limite_disponibilidad_por_minuto", 60)
_DISP_CTA = ("disponibilidad-cuenta", "cuenta", "limite_disponibilidad_por_minuto", 60)

ENTRADAS: tuple[Entrada, ...] = (
    Entrada("E01", ("GET",), "/disponibilidad", "Franjas libres por especialidad, establecimiento y fecha", "anonimo (sin datos personales)", "No aplica",
            "Parámetros en lista blanca; paginación; caché 30 s; 60 por minuto y por IP", "Lectura: no aplica", "R21, R12, T09", (_DISP_IP, _DISP_CTA)),
    Entrada("E02", ("GET", "POST"), "/cuentas/activar", "Activar la cuenta con el código de un solo uso (GET muestra el formulario)", "credenciales (código, Anexo F)",
            "El código pertenece a esa cédula", "5 intentos por código; mensajes genéricos; límites por IP", "Token CSRF", "R05, R18, T03"),
    Entrada("E03", ("GET", "POST"), "/sesion", "Iniciar sesión con cédula y clave (GET muestra el formulario)", "credenciales", "No aplica",
            "Bloqueo progresivo; respuesta idéntica ante cédula inexistente; 10 por minuto y por IP", "Token CSRF", "R05, R18, R19, T03"),
    Entrada("E04", ("GET", "POST"), "/sesion/segundo-factor", "Verificar TOTP o código de recuperación", "sesion parcial (tras la clave)",
            "La sesión parcial es de esa cuenta", "5 intentos y bloqueo; 10 por minuto y por IP", "Token CSRF", "R05, T03"),
    Entrada("E05", ("POST",), "/sesion/cerrar", "Cerrar sesión (la actual, una de las propias o todas)", "sesion", "Propia", "-", "Token CSRF", "R19, T03", (_API,)),
    Entrada("E06", ("GET",), "/turnos", "Lista de turnos del titular o de un representado", "sesion", "Propios o con delegación vigente; el titular se valida en el servidor",
            "Paginación; filtros en lista blanca", "Lectura", "R15, R04, T13, T02", (_API,)),
    Entrada("E07", ("GET",), "/turnos/{id}", "Detalle de un turno", "sesion",
            "El turno es del titular o de un representado con delegación vigente; id no secuencial (UUID); 404 igual para inexistente y ajeno",
            "Formato del id validado", "Lectura", "R15, T13", (_API,)),
    Entrada("E08", ("POST",), "/turnos", "Reservar un turno (RT-01 a RT-12)", "sesion", "Titular o representante con delegación; reglas de negocio en el servidor",
            "Esquema estricto (campos desconocidos se rechazan); 10 por hora y por cuenta; cuerpo máximo 64 KB", "Clave de idempotencia obligatoria; token CSRF",
            "R12, R13, T07, T16", (_API,)),
    Entrada("E09", ("POST",), "/turnos/{id}/confirmar", "Confirmar dentro de las 24 horas", "sesion", "Objeto del titular o representante", "Solo desde RESERVADO",
            "Idempotente; token CSRF", "R14, T17", (_API,)),
    Entrada("E10", ("POST",), "/turnos/{id}/cancelar", "Cancelar hasta 2 horas antes (o deshacer en 10 minutos)", "sesion", "Objeto del titular o representante",
            "Confirmación explícita", "Idempotente; token CSRF", "R14, T17", (_API,)),
    Entrada("E11", ("POST",), "/turnos/{id}/reprogramar", "Reprogramar (máximo 2 veces)", "sesion", "Objeto del titular o representante", "Contador por turno",
            "Clave de idempotencia; token CSRF", "R14, R13, T17", (_API,)),
    Entrada("E12", ("POST",), "/turnos/{id}/apelar", "Apelar una restricción por inasistencias", "sesion", "Restricción del propio titular", "Texto limitado y escapado",
            "Token CSRF", "R14, T17", (_API,)),
    Entrada("E13", ("POST",), "/delegaciones", "Crear una delegación", "sesion con segundo factor reciente", "Solo el titular o el tutor verificado",
            "Máximo 5 representados por representante", "Token CSRF", "R04, T02", (_API,)),
    Entrada("E14", ("DELETE", "POST"), "/delegaciones/{id}", "Revocar una delegación (formulario HTML: POST con _metodo=DELETE)", "sesion", "Titular o representante",
            "-", "Idempotente; token CSRF", "R04, T02", (_API,)),
    Entrada("E15", ("GET", "POST"), "/mis-datos/solicitudes", "Acceso, rectificación, eliminación u oposición (GET lista las propias)", "sesion con segundo factor reciente",
            "Solo el titular", "Registro de la solicitud; respuesta en 10 y 15 días", "Token CSRF", "N03, T11", (_API,)),
    Entrada("E16", ("POST",), "/interno/turnos/{id}/asistencia", "Recepción registra asistió, no asistió o justificación", "funcionario con segundo factor",
            "Rol recepción y misma unidad", "Solo adición; el cambio queda auditado", "Idempotente; token CSRF", "R14, R22, T04, T11b", (_API,)),
    Entrada("E17", ("GET",), "/interno/agenda", "Agenda de la unidad", "funcionario con segundo factor", "Rol y unidad; sin datos clínicos",
            "Paginación; sin exportación masiva", "Lectura", "R06, T04", (_API,)),
    Entrada("E18", ("GET",), "/interno/afiliados/{cedula}", "Consulta de contacto por recepción o soporte", "funcionario con segundo factor",
            "Datos enmascarados; completos solo con justificación registrada", "Máximo 30 consultas por hora y por funcionario",
            "Lectura; auditada", "R06, R08, T04, T06", (_API,)),
    Entrada("E19", ("GET",), "/auditoria", "Consulta de registros", "auditor y DPD con segundo factor", "Solo lectura; sin secretos", "Filtros en lista blanca; paginación",
            "Lectura", "R22, T11b", (_API,)),
    Entrada("E20", ("GET",), "/enlaces/{token}", "Confirmación desde un aviso", "token de un solo uso", "El token pertenece a un turno; no abre datos",
            "Caduca en 24 h; invalidado tras el uso", "Redirige a iniciar sesión", "R03, T05"),
    Entrada("E21", ("GET",), "/salud", "Verificación de estado del servicio", "solo red interna (el proxy lo bloquea desde fuera)", "No aplica", "Sin datos ni versiones",
            "Lectura", "R09, T08"),
    # --- Rutas de soporte (no numeradas en el Anexo D) ---
    Entrada("S01", ("GET",), "/", "Página de inicio", "anonimo", "No aplica", "-", "Lectura", "-", soporte=True),
    Entrada("S02", ("GET", "POST"), "/cuenta/activar/", "Solicitar el código de activación (Anexo F)", "anonimo", "El código va solo al contacto registrado en afiliación",
            "5 solicitudes por hora por cédula y 20 por IP; respuesta idéntica exista o no la cédula", "Token CSRF", "R05, R18, CA-04, CA-05", soporte=True),
    Entrada("S03", ("GET", "POST"), "/cuenta/mfa/configurar/", "Configurar el segundo factor (TOTP)", "sesion o sesion parcial", "Propia; no se puede reemplazar un factor con solo la clave",
            "10 por minuto y por IP", "Token CSRF", "R05, T03", soporte=True),
    Entrada("S04", ("GET", "POST"), "/cuenta/reautenticar/", "Confirmar clave y segundo factor para una operación sensible", "sesion", "Propia",
            "10 por minuto y por IP; alimenta el bloqueo", "Token CSRF", "R05, R19", (_API,), soporte=True),
    Entrada("S05", ("GET",), "/cuenta/sesiones/", "Listar las sesiones propias", "sesion", "Propias", "-", "Lectura", "R19", (_API,), soporte=True),
    Entrada("S07", ("GET", "POST"), "/cuenta/clave/cambiar/", "Cambiar la clave (cierra todas las sesiones)", "sesion con segundo factor reciente", "Propia",
            "-", "Token CSRF", "R05, R19", (_API,), soporte=True),
    Entrada("S08", ("GET", "POST"), "/cuenta/recuperar/", "Pedir el enlace de recuperación de clave (Anexo F)", "anonimo", "El enlace va solo al contacto registrado",
            "5 por hora por cédula y 20 por IP; respuesta idéntica", "Token CSRF", "R03, R18, CA-05, CA-16", soporte=True),
    Entrada("S09", ("GET", "POST"), "/cuenta/restablecer/{token}/", "Elegir una clave nueva con el token", "token de un solo uso (128 bits, 15 min)",
            "El token pertenece a esa cuenta; con 2.º factor si lo tiene", "Un solo uso consumido en transacción", "Token CSRF", "R03, R19, CA-06", soporte=True),
    Entrada("S10", ("GET",), "/delegaciones", "Ver y gestionar las delegaciones propias (formulario de alta)", "sesion", "Propias", "-", "Lectura", "R04", (_API,), soporte=True),
)

INVENTARIO = {e.id: e for e in ENTRADAS}

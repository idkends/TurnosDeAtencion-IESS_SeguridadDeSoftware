"""Servicios de turnos: reglas RT-01 a RT-12, política de inasistencias y tareas de vencimiento.

Aquí vive TODA la lógica de negocio de los turnos y es el único camino de escritura al ORM. Las vistas solo llaman a una
función de este módulo. Las reglas críticas se imponen además en la base de datos (ver `modelos.py`): si algo de aquí
fallara, el motor rechazaría una segunda reserva activa, una franja ocupada o un solape de horario.

Orden de la validación de una reserva (Figura 2): sesión y límites -> titular o delegación -> restricción -> topes y
solape -> afiliación -> transacción atómica (bloqueo de la franja) -> confirmación, aviso y auditoría.
"""
import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from django.core.cache import caches
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone as zona

from apps.auditoria import servicios as auditoria
from apps.auditoria.servicios import Acciones
from apps.cuentas import politicas
from apps.cuentas import servicios as cuentas
from apps.cuentas.servicios import AccesoDenegado, LimiteExcedido, NoEncontrado, ReautenticacionRequerida  # noqa: F401
from apps.delegaciones import servicios as delegaciones
from apps.integraciones import servicios as integraciones
from apps.notificaciones import servicios as notificaciones
from config import reloj
from config.parametros import P

from .modelos import (
    Apelacion,
    Especialidad,
    Establecimiento,
    EstadoApelacion,
    EstadoFranja,
    EstadoTurno,
    Franja,
    MotivoTurno,
    Restriccion,
    SolicitudReserva,
    Turno,
    TipoRestriccion,
    ESTADOS_ACTIVOS,
)

__all__ = [
    "AccesoDenegado", "ConflictoIdempotencia", "LimiteExcedido", "NoEncontrado", "ParametroInvalido", "ReautenticacionRequerida",
    "ReglaRechazada", "FranjaVista", "TurnoVista", "asegurar_catalogos", "generar_franjas", "buscar_disponibilidad", "catalogos",
    "reservar", "listar_turnos", "detalle_turno", "confirmar_turno", "cancelar_turno", "deshacer_cancelacion", "reprogramar_turno",
    "apelar_restriccion", "resolver_apelacion", "restricciones_de", "registrar_atencion", "agenda_unidad", "consultar_afiliado",
    "expirar_vencidos", "conciliar_pendientes", "recalcular_restricciones", "pantalla_disponibilidad", "pantalla_turnos",
]

MENSAJE_GENERICO = (
    "No fue posible completar la operación con las reglas vigentes. Si lo necesita, puede gestionar su cita de forma "
    "presencial o por teléfono."
)
CAUSAS = {
    "INASISTENCIA_INJUSTIFICADA": "Una inasistencia sin justificar en los últimos 90 días: solo puede tener 1 cita activa.",
    "INASISTENCIAS_CONSECUTIVAS": "Dos inasistencias consecutivas: la reserva en línea de esta especialidad está bloqueada temporalmente.",
}
ESTADO_TEXTO = {e.value: e.label for e in EstadoTurno}
_CLAVE_IDEMPOTENCIA = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


# --- Excepciones de dominio --------------------------------------------------------------------------------------------------------


class ErrorTurnos(Exception):
    pass


class ReglaRechazada(ErrorTurnos):
    """La operación incumple una regla. `mensaje` es genérico (no revela cédulas ni estados ajenos); `codigo` es para auditoría."""

    def __init__(self, codigo: str, mensaje: str = MENSAJE_GENERICO, *, ofrecer_presencial: bool = False, hasta=None):
        super().__init__(codigo)
        self.codigo, self.mensaje, self.ofrecer_presencial, self.hasta = codigo, mensaje, ofrecer_presencial, hasta


class ConflictoIdempotencia(ErrorTurnos):
    """La misma clave de idempotencia se usó con otra solicitud distinta."""


class ParametroInvalido(ErrorTurnos):
    pass


# --- Vistas de datos (lo que reciben las plantillas: sin consultas ni lógica) -------------------------------------------------------


@dataclass(frozen=True)
class FranjaVista:
    id: object
    inicio: datetime
    fin: datetime
    especialidad: str
    establecimiento: str


@dataclass(frozen=True)
class PaginaDisponibilidad:
    franjas: list
    pagina: int
    tamano: int
    total: int

    @property
    def hay_siguiente(self) -> bool:
        return self.pagina * self.tamano < self.total


@dataclass(frozen=True)
class TurnoVista:
    id: object
    estado: str
    estado_texto: str
    afiliado_id: object
    afiliado_nombre: str
    especialidad: str
    establecimiento: str
    inicio: datetime
    fin: datetime
    confirmar_antes_de: datetime | None
    reprogramaciones: int
    reprogramaciones_restantes: int
    puede_confirmar: bool = False
    puede_cancelar: bool = False
    puede_reprogramar: bool = False
    puede_deshacer: bool = False
    puede_apelar: bool = False
    deshacer_hasta: datetime | None = None
    alternativas: list = field(default_factory=list)


@dataclass(frozen=True)
class RestriccionVista:
    id: object
    tipo: str
    causa: str
    causa_texto: str
    fin: datetime
    canal_alterno: str = "La atención presencial y telefónica siempre está disponible."


@dataclass(frozen=True)
class PaginaTurnos:
    turnos: list
    pagina: int
    tamano: int
    total: int

    @property
    def hay_siguiente(self) -> bool:
        return self.pagina * self.tamano < self.total


@dataclass(frozen=True)
class FilaAgenda:
    turno_id: object
    inicio: datetime
    especialidad: str
    estado: str
    estado_texto: str
    persona: str
    cedula_enmascarada: str
    puede_registrar: bool
    justificable: bool


@dataclass(frozen=True)
class PaginaAgenda:
    filas: list
    fecha: date
    pagina: int
    tamano: int
    total: int

    @property
    def hay_siguiente(self) -> bool:
        return self.pagina * self.tamano < self.total


@dataclass(frozen=True)
class ContactoVista:
    nombre: str
    cedula: str
    correo: str
    telefono: str
    completo: bool


# --- Utilidades ----------------------------------------------------------------------------------------------------------------------------


def _paginar(pagina, tamano):
    tamano = max(1, min(tamano or P.paginacion_por_defecto, P.paginacion_maxima))
    return max(1, int(pagina or 1)), tamano


def _fmt_fecha(instante) -> str:
    return zona.localtime(instante).strftime("%d/%m/%Y a las %H:%M")


def _dias_habiles(desde: datetime, n: int) -> datetime:
    actual = desde
    while n > 0:
        actual += timedelta(days=1)
        if actual.weekday() < 5:
            n -= 1
    return actual


def _huella(*partes) -> str:
    return hashlib.sha256("|".join(str(p) for p in partes).encode()).hexdigest()


def _codigo_integridad(error: IntegrityError) -> str:
    texto = str(error)
    for nombre, codigo in (
        ("uq_turno_afiliado_especialidad", "YA_TIENE_CITA_ESPECIALIDAD"),
        ("uq_turno_franja", "FRANJA_OCUPADA"),
        ("ex_turno_sin_solape", "SOLAPE_DE_HORARIO"),
        ("uq_idempotencia", "IDEMPOTENCIA"),
    ):
        if nombre in texto:
            return codigo
    return "RESTRICCION_DE_BASE"


def _aud(accion, solicitante, turno=None, *, resultado=auditoria.OK, **detalle):
    """Auditoría de una operación de turno. Si opera un representante, quedan AMBOS identificadores."""
    if turno is not None:
        detalle.setdefault("afiliado_id", str(turno.afiliado_id))
        if solicitante is not None and solicitante.id != turno.afiliado_id:
            detalle["representante_id"] = str(solicitante.id)
    auditoria.registrar(
        accion=accion, actor=solicitante, resultado=resultado, recurso_tipo="turno", recurso_id=getattr(turno, "id", ""), detalle=detalle
    )


def _rechazar(solicitante, codigo, *, afiliado_id=None, accion=Acciones.RESERVA_RECHAZADA, **extra) -> None:
    """Audita el rechazo (FUERA de cualquier transacción revertida) y lanza la excepción con mensaje genérico."""
    auditoria.registrar(
        accion=accion, actor=solicitante, resultado=auditoria.DENEGADO, recurso_tipo="turno",
        detalle={"codigo": codigo, "afiliado_id": str(afiliado_id or ""), **{k: str(v) for k, v in extra.items()}},
    )


def _notificar(afiliado_id, plantilla: str, **parametros) -> None:
    persona = cuentas.persona_por_id(afiliado_id)
    contacto = cuentas.contacto_por_cedula(persona.cedula) if persona else None
    if contacto is not None:
        notificaciones.enviar(plantilla=plantilla, canal="CORREO", destino=contacto.correo, parametros=parametros, destinatario_id=afiliado_id)


def _avisar_representante(solicitante, turno) -> None:
    if solicitante.id != turno.afiliado_id:
        delegaciones.notificar_accion_representante(turno.afiliado_id, solicitante.id)


def _franja_vista(f: Franja) -> FranjaVista:
    return FranjaVista(f.id, f.inicio, f.fin, f.especialidad.nombre, f.establecimiento.nombre)


# --- Catálogos y oferta ---------------------------------------------------------------------------------------------------------------------

CATALOGO_ESTABLECIMIENTOS = (
    ("QUI01", "Centro de Salud Quito Norte (ficticio)"),
    ("GUA01", "Centro de Salud Guayaquil Sur (ficticio)"),
)
CATALOGO_ESPECIALIDADES = (
    ("MGEN", "Medicina general"),
    ("PEDI", "Pediatría"),
    ("GINE", "Ginecología"),
    ("ODON", "Odontología"),
    ("CARD", "Cardiología"),
)
HORAS_JORNADA = ((8, 0), (8, 30), (9, 0), (9, 30), (10, 0), (10, 30))  # 6 franjas de 30 minutos por día laborable
DURACION_FRANJA = timedelta(minutes=30)


def asegurar_catalogos() -> tuple[list[Establecimiento], list[Especialidad]]:
    """Crea (si faltan) los 2 establecimientos y las 5 especialidades de demostración. Idempotente."""
    with transaction.atomic():
        establecimientos = [
            Establecimiento.objects.get_or_create(codigo=codigo, defaults={"nombre": nombre})[0] for codigo, nombre in CATALOGO_ESTABLECIMIENTOS
        ]
        especialidades = [
            Especialidad.objects.get_or_create(codigo=codigo, defaults={"nombre": nombre})[0] for codigo, nombre in CATALOGO_ESPECIALIDADES
        ]
    return establecimientos, especialidades


def generar_franjas(establecimientos, especialidades, dias: int | None = None) -> int:
    """Genera franjas libres de lunes a viernes para los próximos `dias` días (por defecto, la ventana de reserva).

    Una franja de cada jornada queda reservada a atención prioritaria (RT-11). Idempotente por la restricción única.
    """
    dias = dias or P.turnos_dias_anticipacion
    hoy = zona.localdate(reloj.ahora())
    creadas = 0
    with transaction.atomic():
        for desplazamiento in range(1, dias + 1):
            dia = hoy + timedelta(days=desplazamiento)
            if dia.weekday() >= 5:
                continue
            for est in establecimientos:
                for esp in especialidades:
                    for indice, (hora, minuto) in enumerate(HORAS_JORNADA):
                        inicio = zona.make_aware(datetime.combine(dia, time(hora, minuto)))
                        _, nueva = Franja.objects.get_or_create(
                            establecimiento=est, especialidad=esp, inicio=inicio,
                            defaults={"fin": inicio + DURACION_FRANJA, "estado": EstadoFranja.LIBRE, "cupo_prioritario": indice == len(HORAS_JORNADA) - 1},
                        )
                        creadas += nueva
        auditoria.registrar(accion=Acciones.SIEMBRA, recurso_tipo="franja", detalle={"franjas_creadas": creadas, "dias": dias})
    return creadas


def catalogos() -> dict:
    return {
        "especialidades": list(Especialidad.objects.filter(activa=True).order_by("nombre").values_list("codigo", "nombre")),
        "establecimientos": list(Establecimiento.objects.filter(activo=True).order_by("nombre").values_list("codigo", "nombre")),
    }


# --- E01: disponibilidad (pública, sin datos personales) ------------------------------------------------------------------------------------


def buscar_disponibilidad(*, especialidad=None, establecimiento=None, fecha: date | None = None, pagina=1, tamano=None, usuario=None) -> PaginaDisponibilidad:
    """Franjas libres. Parámetros en lista blanca (códigos del catálogo y fecha); paginado; con caché corta (30 s)."""
    pagina, tamano = _paginar(pagina, tamano)
    cat = catalogos()
    if especialidad and especialidad not in {c for c, _ in cat["especialidades"]}:
        raise ParametroInvalido("especialidad")
    if establecimiento and establecimiento not in {c for c, _ in cat["establecimientos"]}:
        raise ParametroInvalido("establecimiento")
    incluir_prioritarias = bool(usuario is not None and getattr(usuario, "is_authenticated", False) and cuentas.es_prioritaria(usuario.id))
    clave = "disp:" + _huella(especialidad, establecimiento, fecha, pagina, tamano, incluir_prioritarias)
    cache = caches["default"]
    en_cache = cache.get(clave)
    if en_cache is not None:
        return en_cache
    ahora = reloj.ahora()
    consulta = Franja.objects.select_related("especialidad", "establecimiento").filter(
        estado=EstadoFranja.LIBRE, inicio__gt=ahora, inicio__lte=ahora + timedelta(days=P.turnos_dias_anticipacion)
    )
    if not incluir_prioritarias:
        consulta = consulta.filter(cupo_prioritario=False)
    if especialidad:
        consulta = consulta.filter(especialidad__codigo=especialidad)
    if establecimiento:
        consulta = consulta.filter(establecimiento__codigo=establecimiento)
    if fecha:
        inicio_dia = zona.make_aware(datetime.combine(fecha, time.min))
        consulta = consulta.filter(inicio__gte=inicio_dia, inicio__lt=inicio_dia + timedelta(days=1))
    total = consulta.count()
    desde = (pagina - 1) * tamano
    resultado = PaginaDisponibilidad([_franja_vista(f) for f in consulta.order_by("inicio", "id")[desde : desde + tamano]], pagina, tamano, total)
    cache.set(clave, resultado, timeout=30)
    return resultado


# --- Restricciones y topes ------------------------------------------------------------------------------------------------------------------


def _restricciones_activas(afiliado_id, ahora):
    return Restriccion.objects.filter(afiliado_id=afiliado_id, revertida_en__isnull=True, inicio__lte=ahora, fin__gt=ahora)


def restricciones_de(solicitante, afiliado_id=None) -> list[RestriccionVista]:
    """Restricciones vigentes con su causa y su fecha de fin (propias o de un representado con delegación vigente)."""
    afiliado_id = afiliado_id or solicitante.id
    _resolver_afiliado(solicitante, afiliado_id, auditar=False)
    return [
        RestriccionVista(r.id, r.tipo, r.causa, CAUSAS.get(r.causa, "Restricción temporal."), r.fin)
        for r in _restricciones_activas(afiliado_id, reloj.ahora()).order_by("fin")
    ]


def _resolver_afiliado(solicitante, afiliado_id, *, auditar=True):
    """Titular, o representante con delegación vigente: devuelve la persona atendida. Si no, 403 (CA-01)."""
    try:
        persona = cuentas.persona_por_id(afiliado_id)
    except (ValueError, TypeError, ValidationError):
        persona = None
    if persona is None or persona.estado != "ACTIVA":
        raise NoEncontrado()
    vigente = False if persona.id == solicitante.id else delegaciones.delegacion_vigente(persona.id, solicitante.id)
    if not politicas.puede_actuar_por(solicitante, persona.id, delegacion_vigente=vigente):
        if auditar:
            _rechazar(solicitante, "SIN_DELEGACION", afiliado_id=persona.id)
        raise AccesoDenegado()
    return persona


# --- Vista de turno ----------------------------------------------------------------------------------------------------------------------------


def _vista_turno(t: Turno, solicitante, *, delegacion_vigente: bool, nombres=None) -> TurnoVista:
    ahora = reloj.ahora()
    gestor = politicas.puede_gestionar_turno(solicitante, t, delegacion_vigente=delegacion_vigente)
    faltan = t.inicio - ahora
    margen = timedelta(hours=P.turnos_horas_minimas_para_cancelar)
    puede_apelar = False
    if t.estado == EstadoTurno.NO_ASISTIO and politicas.puede_apelar(solicitante, t.afiliado_id):
        puede_apelar = t.restricciones_derivadas.filter(revertida_en__isnull=True, fin__gt=ahora).exists()
    deshacer_hasta = t.cancelado_en + timedelta(minutes=P.turnos_deshacer_cancelacion_minutos) if t.cancelado_en else None
    nombre = (nombres or {}).get(t.afiliado_id)
    if nombre is None:
        persona = cuentas.persona_por_id(t.afiliado_id)
        nombre = persona.nombre_completo if persona else ""
    return TurnoVista(
        id=t.id,
        estado=t.estado,
        estado_texto=ESTADO_TEXTO.get(t.estado, t.estado),
        afiliado_id=t.afiliado_id,
        afiliado_nombre=nombre,
        especialidad=t.especialidad.nombre,
        establecimiento=t.franja.establecimiento.nombre,
        inicio=t.inicio,
        fin=t.fin,
        confirmar_antes_de=t.confirmar_antes_de,
        reprogramaciones=t.reprogramaciones,
        reprogramaciones_restantes=max(0, P.turnos_reprogramaciones_maximas - t.reprogramaciones),
        puede_confirmar=bool(gestor and t.estado == EstadoTurno.RESERVADO and t.confirmar_antes_de and ahora <= t.confirmar_antes_de),
        puede_cancelar=bool(gestor and (t.estado in (EstadoTurno.PENDIENTE, EstadoTurno.RESERVADO) or (t.estado == EstadoTurno.CONFIRMADO and faltan >= margen))),
        puede_reprogramar=bool(gestor and t.estado in (EstadoTurno.RESERVADO, EstadoTurno.CONFIRMADO) and t.reprogramaciones < P.turnos_reprogramaciones_maximas and faltan >= margen),
        puede_deshacer=bool(gestor and t.estado == EstadoTurno.CANCELADO_AFILIADO and deshacer_hasta and ahora <= deshacer_hasta),
        puede_apelar=puede_apelar,
        deshacer_hasta=deshacer_hasta,
    )


def _turno_con_relaciones():
    return Turno.objects.select_related("franja", "franja__establecimiento", "especialidad")


def _cargar_turno(solicitante, turno_id, *, para="gestionar", bloquear=False) -> tuple[Turno, bool]:
    """Turno al que la persona tiene acceso. Inexistente y ajeno dan la MISMA respuesta (NoEncontrado -> 404, CA-07)."""
    try:
        consulta = _turno_con_relaciones().filter(pk=turno_id)
        if bloquear:
            consulta = consulta.select_for_update(of=("self",))
        t = consulta.first()
    except (ValueError, TypeError, ValidationError):
        t = None
    if t is None:
        raise NoEncontrado()
    vigente = False if t.afiliado_id == solicitante.id else delegaciones.delegacion_vigente(t.afiliado_id, solicitante.id)
    if para == "gestionar":
        permitido = politicas.puede_gestionar_turno(solicitante, t, delegacion_vigente=vigente)
    else:
        permitido = politicas.puede_ver_turno(solicitante, t, delegacion_vigente=vigente)
    if not permitido:
        _aud(Acciones.ACCESO_DENEGADO, solicitante, t, resultado=auditoria.DENEGADO, accion_intentada=para)
        raise NoEncontrado()
    return t, vigente


# --- E06 / E07: lectura -------------------------------------------------------------------------------------------------------------------------


def listar_turnos(solicitante, *, afiliado_id=None, estado=None, pagina=1, tamano=None) -> PaginaTurnos:
    """Turnos del titular o de un representado (la delegación se valida en el servidor). Filtros en lista blanca."""
    pagina, tamano = _paginar(pagina, tamano)
    if estado is not None and estado not in ESTADO_TEXTO:
        raise ParametroInvalido("estado")
    if not politicas.puede_reservar_en_linea(solicitante):
        raise AccesoDenegado()
    objetivo = afiliado_id or solicitante.id
    persona = _resolver_afiliado(solicitante, objetivo)
    vigente = persona.id != solicitante.id
    consulta = _turno_con_relaciones().filter(afiliado_id=persona.id)
    if estado:
        consulta = consulta.filter(estado=estado)
    total = consulta.count()
    desde = (pagina - 1) * tamano
    filas = consulta.order_by("-inicio", "id")[desde : desde + tamano]
    return PaginaTurnos([_vista_turno(t, solicitante, delegacion_vigente=vigente, nombres={persona.id: persona.nombre_completo}) for t in filas], pagina, tamano, total)


def detalle_turno(solicitante, turno_id) -> TurnoVista:
    """E07. Si el turno se puede reprogramar, incluye franjas alternativas de la misma oferta."""
    t, vigente = _cargar_turno(solicitante, turno_id, para="ver")
    vista = _vista_turno(t, solicitante, delegacion_vigente=vigente)
    if vista.puede_reprogramar:
        alternativas = buscar_disponibilidad(especialidad=t.especialidad.codigo, establecimiento=t.franja.establecimiento.codigo, usuario=solicitante).franjas
        vista = TurnoVista(**{**vista.__dict__, "alternativas": [f for f in alternativas if f.id != t.franja_id]})
    return vista


# --- E08: reservar ---------------------------------------------------------------------------------------------------------------------------------


def reservar(*, solicitante, franja_id, motivo, clave_idempotencia, afiliado_id=None) -> tuple[TurnoVista, bool]:
    """Reserva atómica. Devuelve (turno, creado). Un reintento con la misma clave devuelve el MISMO turno (creado=False)."""
    if not politicas.puede_reservar_en_linea(solicitante):
        _rechazar(solicitante, "ROL_NO_PERMITIDO")
        raise AccesoDenegado()
    if not _CLAVE_IDEMPOTENCIA.match(clave_idempotencia or ""):
        raise ReglaRechazada("CLAVE_INVALIDA", "La clave de idempotencia no es válida.")
    if motivo and motivo not in {m.value for m in MotivoTurno}:
        raise ReglaRechazada("MOTIVO_INVALIDO", "El motivo no es válido.")
    cuentas.limitar_por_cuenta("reservas", solicitante.id, P.limite_reservas_por_hora_cuenta, 3600)  # 10/hora por cuenta (RT-09)

    # 2. Titular, o representante con delegación vigente. RT-08: los topes se aplican a la persona ATENDIDA.
    persona = _resolver_afiliado(solicitante, afiliado_id or solicitante.id)
    huella = _huella("RESERVAR", franja_id, motivo)

    previa = SolicitudReserva.objects.filter(afiliado_id=persona.id, clave_idempotencia=clave_idempotencia).select_related("turno").first()
    if previa is not None:
        return _repetir(solicitante, previa, huella)

    ahora = reloj.ahora()
    try:
        franja = Franja.objects.select_related("especialidad", "establecimiento").filter(pk=franja_id).first()
    except (ValueError, TypeError, ValidationError):
        franja = None
    prioritaria = persona.atencion_prioritaria
    if franja is None or franja.estado != EstadoFranja.LIBRE or franja.inicio <= ahora or (franja.cupo_prioritario and not prioritaria):
        _rechazar(solicitante, "FRANJA_NO_DISPONIBLE", afiliado_id=persona.id)
        raise ReglaRechazada("FRANJA_NO_DISPONIBLE")
    if franja.inicio > ahora + timedelta(days=P.turnos_dias_anticipacion):
        _rechazar(solicitante, "FUERA_DE_VENTANA", afiliado_id=persona.id)
        raise ReglaRechazada("FUERA_DE_VENTANA", f"Solo se puede reservar con hasta {P.turnos_dias_anticipacion} días de anticipación.")

    # 3. Restricción por inasistencias vigente (no aplica a grupos de atención prioritaria, RT-11).
    activas = _restricciones_activas(persona.id, ahora)
    if not prioritaria:
        bloqueo = activas.filter(tipo=TipoRestriccion.BLOQUEO_RESERVA, especialidad_id=franja.especialidad_id).first()
        if bloqueo is not None:
            _rechazar(solicitante, "RESTRICCION_VIGENTE", afiliado_id=persona.id)
            raise ReglaRechazada(
                "RESTRICCION_VIGENTE",
                f"La reserva en línea de esta especialidad está bloqueada hasta el {_fmt_fecha(bloqueo.fin)}. "
                "Puede reservar por el canal presencial o telefónico y apelar ante una persona de su unidad.",
                ofrecer_presencial=True, hasta=bloqueo.fin,
            )

    # 4. Turno activo en la especialidad, tope de turnos activos o solape (mensaje genérico, RT-01/02/03).
    tope_total = P.turnos_activos_totales
    if not prioritaria and activas.filter(tipo=TipoRestriccion.TOPE_REDUCIDO).exists():
        tope_total = P.inasistencias_tope_reducido
    activos = Turno.objects.filter(afiliado_id=persona.id, estado__in=ESTADOS_ACTIVOS)
    if activos.filter(especialidad_id=franja.especialidad_id).count() >= P.turnos_activos_por_especialidad:
        _rechazar(solicitante, "YA_TIENE_CITA_ESPECIALIDAD", afiliado_id=persona.id)
        raise ReglaRechazada("YA_TIENE_CITA_ESPECIALIDAD")
    if activos.count() >= tope_total:
        _rechazar(solicitante, "TOPE_DE_CITAS", afiliado_id=persona.id)
        raise ReglaRechazada("TOPE_DE_CITAS")
    if activos.filter(inicio__lt=franja.fin, fin__gt=franja.inicio).exists():
        _rechazar(solicitante, "SOLAPE_DE_HORARIO", afiliado_id=persona.id)
        raise ReglaRechazada("SOLAPE_DE_HORARIO")

    # 5. Afiliación vigente. Si el servicio falla NO se afirma cobertura: el turno queda PENDIENTE (RT-10).
    afiliacion_disponible = True
    try:
        if not integraciones.consultar_vigencia(persona.cedula):
            _rechazar(solicitante, "SIN_AFILIACION_VIGENTE", afiliado_id=persona.id)
            raise ReglaRechazada("SIN_AFILIACION_VIGENTE")
    except integraciones.ServicioNoDisponible:
        afiliacion_disponible = False
        auditoria.registrar(accion=Acciones.AFILIACION_NO_DISPONIBLE, actor=solicitante, resultado=auditoria.ERROR, recurso_tipo="turno")

    # 6. Transacción atómica: bloquear la franja, comprobar LIBRE, insertar turno y clave de idempotencia.
    codigo_error = None
    turno_id = None
    try:
        with transaction.atomic():
            bloqueada = Franja.objects.select_for_update().get(pk=franja.pk)
            if bloqueada.estado != EstadoFranja.LIBRE:
                raise ReglaRechazada("FRANJA_OCUPADA")
            with transaction.atomic():  # punto de guardado: una violación de restricción no envenena la transacción externa
                turno = Turno.objects.create(
                    afiliado_id=persona.id, solicitante_id=solicitante.id, especialidad_id=franja.especialidad_id, franja_id=franja.pk,
                    estado=EstadoTurno.PENDIENTE, inicio=franja.inicio, fin=franja.fin, motivo=motivo or "",
                )
                SolicitudReserva.objects.create(
                    afiliado_id=persona.id, solicitante_id=solicitante.id, clave_idempotencia=clave_idempotencia, huella=huella, turno=turno
                )
            bloqueada.estado = EstadoFranja.OCUPADA
            bloqueada.save(update_fields=["estado"])
            turno_id = turno.id
            _aud(Acciones.RESERVA_CREADA, solicitante, turno, franja_id=str(franja.pk))
    except ReglaRechazada as error:
        codigo_error = error.codigo
    except IntegrityError as error:
        codigo_error = _codigo_integridad(error)
    if codigo_error == "IDEMPOTENCIA":  # carrera: otra solicitud con la misma clave ganó entre la consulta y la inserción
        previa = SolicitudReserva.objects.filter(afiliado_id=persona.id, clave_idempotencia=clave_idempotencia).select_related("turno").first()
        if previa is not None:
            return _repetir(solicitante, previa, huella)
    if codigo_error:
        _rechazar(solicitante, codigo_error, afiliado_id=persona.id)
        raise ReglaRechazada(codigo_error)

    _avisar_representante(solicitante, Turno(afiliado_id=persona.id))
    if afiliacion_disponible:
        _confirmar_en_agenda(turno_id)
    t = _turno_con_relaciones().get(pk=turno_id)
    return _vista_turno(t, solicitante, delegacion_vigente=persona.id != solicitante.id), True


def _repetir(solicitante, previa: SolicitudReserva, huella: str) -> tuple[TurnoVista, bool]:
    if previa.huella != huella or previa.turno_id is None:
        _rechazar(solicitante, "IDEMPOTENCIA_CONFLICTO", afiliado_id=previa.afiliado_id)
        raise ConflictoIdempotencia()
    t, vigente = _cargar_turno(solicitante, previa.turno_id, para="gestionar")
    auditoria.registrar(accion=Acciones.RESERVA_REPETIDA, actor=solicitante, recurso_tipo="turno", recurso_id=t.id)
    return _vista_turno(t, solicitante, delegacion_vigente=vigente), False


def _clave_agenda(t: Turno) -> str:
    return str(t.id) if t.reprogramaciones == 0 else f"{t.id}-r{t.reprogramaciones}"


def _confirmar_en_agenda(turno_id) -> str:
    """PENDIENTE -> RESERVADO cuando el sistema de agenda confirma. Si falla, el turno SIGUE PENDIENTE (nunca se inventa)."""
    t = _turno_con_relaciones().filter(pk=turno_id).first()
    if t is None or t.estado != EstadoTurno.PENDIENTE:
        return t.estado if t else ""
    persona = cuentas.persona_por_id(t.afiliado_id)
    try:
        if not integraciones.consultar_vigencia(persona.cedula):
            _cancelar_por_institucion(t.id, "SIN_AFILIACION_VIGENTE")
            return EstadoTurno.CANCELADO_INSTITUCION
    except integraciones.ServicioNoDisponible:
        auditoria.registrar(accion=Acciones.AFILIACION_NO_DISPONIBLE, resultado=auditoria.ERROR, recurso_tipo="turno", recurso_id=t.id)
        return EstadoTurno.PENDIENTE
    try:
        confirmacion = integraciones.confirmar_en_agenda(
            turno_ref=str(t.id), franja_ref=str(t.franja_id), afiliado_ref=str(t.afiliado_id), clave=_clave_agenda(t)
        )
    except integraciones.ServicioNoDisponible:
        auditoria.registrar(accion=Acciones.AGENDA_NO_DISPONIBLE, resultado=auditoria.ERROR, recurso_tipo="turno", recurso_id=t.id)
        return EstadoTurno.PENDIENTE
    if confirmacion.estado != "CONFIRMADA":
        _cancelar_por_institucion(t.id, "AGENDA_RECHAZO")
        return EstadoTurno.CANCELADO_INSTITUCION
    with transaction.atomic():
        t = Turno.objects.select_for_update().get(pk=t.id)
        if t.estado != EstadoTurno.PENDIENTE:
            return t.estado
        t.estado = EstadoTurno.RESERVADO
        t.confirmar_antes_de = reloj.ahora() + timedelta(hours=P.turnos_horas_para_confirmar)  # RT-05
        t.save(update_fields=["estado", "confirmar_antes_de", "actualizado"])
        _aud(Acciones.TURNO_RESERVADO, None, t, referencia_agenda=confirmacion.referencia)
    _notificar(t.afiliado_id, "reserva_creada")
    return EstadoTurno.RESERVADO


def _cancelar_por_institucion(turno_id, motivo: str) -> None:
    with transaction.atomic():
        t = Turno.objects.select_for_update().get(pk=turno_id)
        if t.estado not in ESTADOS_ACTIVOS:
            return
        t.estado = EstadoTurno.CANCELADO_INSTITUCION
        t.cancelado_en = reloj.ahora()
        t.save(update_fields=["estado", "cancelado_en", "actualizado"])
        Franja.objects.filter(pk=t.franja_id).update(estado=EstadoFranja.LIBRE)
        _aud(Acciones.TURNO_CANCELADO_INSTITUCION, None, t, motivo=motivo)
    integraciones.cancelar_en_agenda(str(turno_id))
    _notificar(t.afiliado_id, "cita_cancelada")


# --- E09 / E10 / E11: confirmar, cancelar, reprogramar --------------------------------------------------------------------------------------


def confirmar_turno(solicitante, turno_id) -> TurnoVista:
    """Confirmar dentro de las 24 h (solo desde RESERVADO). Idempotente."""
    with transaction.atomic():
        t, vigente = _cargar_turno(solicitante, turno_id, bloquear=True)
        if t.estado == EstadoTurno.CONFIRMADO:
            return _vista_turno(t, solicitante, delegacion_vigente=vigente)
        ahora = reloj.ahora()
        if t.estado != EstadoTurno.RESERVADO or (t.confirmar_antes_de and ahora > t.confirmar_antes_de):
            _rechazo = ReglaRechazada("NO_SE_PUEDE_CONFIRMAR", "Este turno ya no se puede confirmar.")
        else:
            _rechazo = None
            t.estado = EstadoTurno.CONFIRMADO
            t.confirmado_en = ahora
            t.save(update_fields=["estado", "confirmado_en", "actualizado"])
            _aud(Acciones.TURNO_CONFIRMADO, solicitante, t)
    if _rechazo:
        _aud(Acciones.RESERVA_RECHAZADA, solicitante, t, resultado=auditoria.DENEGADO, codigo=_rechazo.codigo)
        raise _rechazo
    _avisar_representante(solicitante, t)
    _notificar(t.afiliado_id, "cita_confirmada", fecha=_fmt_fecha(t.inicio))
    return _vista_turno(t, solicitante, delegacion_vigente=vigente)


def cancelar_turno(solicitante, turno_id) -> TurnoVista:
    """Cancelar hasta 2 h antes (RT-07). Libera el cupo de inmediato. Idempotente."""
    rechazo = None
    with transaction.atomic():
        t, vigente = _cargar_turno(solicitante, turno_id, bloquear=True)
        if t.estado == EstadoTurno.CANCELADO_AFILIADO:
            return _vista_turno(t, solicitante, delegacion_vigente=vigente)
        ahora = reloj.ahora()
        tarde = t.estado == EstadoTurno.CONFIRMADO and t.inicio - ahora < timedelta(hours=P.turnos_horas_minimas_para_cancelar)
        if t.estado not in ESTADOS_ACTIVOS or tarde:
            rechazo = ReglaRechazada(
                "CANCELACION_TARDIA" if tarde else "NO_SE_PUEDE_CANCELAR",
                f"Solo se puede cancelar hasta {P.turnos_horas_minimas_para_cancelar} horas antes. Comuníquese con su unidad de atención."
                if tarde else "Este turno ya no se puede cancelar.",
                ofrecer_presencial=tarde,
            )
        else:
            t.estado_previo_cancelacion = t.estado
            t.estado = EstadoTurno.CANCELADO_AFILIADO
            t.cancelado_en = ahora
            t.save(update_fields=["estado", "estado_previo_cancelacion", "cancelado_en", "actualizado"])
            Franja.objects.filter(pk=t.franja_id).update(estado=EstadoFranja.LIBRE)
            _aud(Acciones.TURNO_CANCELADO, solicitante, t)
    if rechazo:
        _aud(Acciones.RESERVA_RECHAZADA, solicitante, t, resultado=auditoria.DENEGADO, codigo=rechazo.codigo)
        raise rechazo
    integraciones.cancelar_en_agenda(str(t.id))
    _avisar_representante(solicitante, t)
    _notificar(t.afiliado_id, "cita_cancelada")
    return _vista_turno(t, solicitante, delegacion_vigente=vigente)


def deshacer_cancelacion(solicitante, turno_id) -> TurnoVista:
    """Deshace una cancelación durante 10 minutos si el cupo sigue libre y la persona no tomó otro turno incompatible."""
    rechazo = None
    with transaction.atomic():
        t, vigente = _cargar_turno(solicitante, turno_id, bloquear=True)
        ahora = reloj.ahora()
        if t.estado != EstadoTurno.CANCELADO_AFILIADO or not t.cancelado_en or ahora - t.cancelado_en > timedelta(minutes=P.turnos_deshacer_cancelacion_minutos):
            rechazo = "PLAZO_VENCIDO"
        else:
            franja = Franja.objects.select_for_update().get(pk=t.franja_id)
            if franja.estado != EstadoFranja.LIBRE or franja.inicio <= ahora:
                rechazo = "CUPO_NO_DISPONIBLE"
            else:
                previo = t.estado_previo_cancelacion or EstadoTurno.RESERVADO
                try:
                    with transaction.atomic():
                        t.estado = EstadoTurno.PENDIENTE  # reconfirma con la agenda; vuelve a su estado anterior al confirmar
                        t.estado_previo_cancelacion = previo
                        t.cancelado_en = None
                        t.save(update_fields=["estado", "estado_previo_cancelacion", "cancelado_en", "actualizado"])
                except IntegrityError as error:
                    rechazo = _codigo_integridad(error)
                else:
                    franja.estado = EstadoFranja.OCUPADA
                    franja.save(update_fields=["estado"])
                    _aud(Acciones.CANCELACION_DESHECHA, solicitante, t, estado_previo=previo)
    if rechazo:
        _aud(Acciones.RESERVA_RECHAZADA, solicitante, t, resultado=auditoria.DENEGADO, codigo=rechazo)
        raise ReglaRechazada(rechazo, "Ya no es posible deshacer la cancelación: el cupo no está disponible o venció el plazo.")
    _restaurar_estado_tras_deshacer(t.id)
    t = _turno_con_relaciones().get(pk=t.id)
    return _vista_turno(t, solicitante, delegacion_vigente=vigente)


def _restaurar_estado_tras_deshacer(turno_id) -> None:
    """Tras deshacer, la agenda vuelve a confirmar el cupo; si falla queda PENDIENTE y la conciliación lo resuelve."""
    t = Turno.objects.get(pk=turno_id)
    previo = t.estado_previo_cancelacion
    try:
        confirmacion = integraciones.confirmar_en_agenda(
            turno_ref=str(t.id), franja_ref=str(t.franja_id), afiliado_ref=str(t.afiliado_id), clave=f"{t.id}-u{int(reloj.ahora().timestamp())}"
        )
    except integraciones.ServicioNoDisponible:
        auditoria.registrar(accion=Acciones.AGENDA_NO_DISPONIBLE, resultado=auditoria.ERROR, recurso_tipo="turno", recurso_id=t.id)
        return
    if confirmacion.estado != "CONFIRMADA":
        _cancelar_por_institucion(t.id, "AGENDA_RECHAZO")
        return
    Turno.objects.filter(pk=t.id, estado=EstadoTurno.PENDIENTE).update(
        estado=previo if previo in (EstadoTurno.RESERVADO, EstadoTurno.CONFIRMADO) else EstadoTurno.RESERVADO,
        confirmar_antes_de=reloj.ahora() + timedelta(hours=P.turnos_horas_para_confirmar) if previo != EstadoTurno.CONFIRMADO else None,
    )


def reprogramar_turno(solicitante, turno_id, nueva_franja_id, clave_idempotencia) -> tuple[TurnoVista, bool]:
    """Mueve el turno a otra franja de la misma especialidad (máximo 2 veces, hasta 2 h antes). Idempotente por clave.

    La reprogramación reinicia la confirmación: el turno pasa por PENDIENTE -> RESERVADO con 24 h para confirmar.
    """
    if not _CLAVE_IDEMPOTENCIA.match(clave_idempotencia or ""):
        raise ReglaRechazada("CLAVE_INVALIDA", "La clave de idempotencia no es válida.")
    t0, _ = _cargar_turno(solicitante, turno_id)
    try:
        nueva_franja_id = _a_uuid(nueva_franja_id)
    except (ValueError, TypeError, AttributeError):
        _aud(Acciones.RESERVA_RECHAZADA, solicitante, t0, resultado=auditoria.DENEGADO, codigo="FRANJA_NO_DISPONIBLE")
        raise ReglaRechazada("FRANJA_NO_DISPONIBLE", "No fue posible reprogramar este turno.", ofrecer_presencial=True) from None
    huella = _huella("REPROGRAMAR", t0.id, nueva_franja_id)
    previa = SolicitudReserva.objects.filter(afiliado_id=t0.afiliado_id, clave_idempotencia=clave_idempotencia).select_related("turno").first()
    if previa is not None:
        vista, _ = _repetir(solicitante, previa, huella)
        return vista, False
    codigo = None
    with transaction.atomic():
        t, vigente = _cargar_turno(solicitante, turno_id, bloquear=True)
        ahora = reloj.ahora()
        if t.estado not in (EstadoTurno.RESERVADO, EstadoTurno.CONFIRMADO):
            codigo = "NO_SE_PUEDE_REPROGRAMAR"
        elif t.reprogramaciones >= P.turnos_reprogramaciones_maximas:
            codigo = "LIMITE_REPROGRAMACIONES"
        elif t.inicio - ahora < timedelta(hours=P.turnos_horas_minimas_para_cancelar):
            codigo = "REPROGRAMACION_TARDIA"
        else:
            # Bloqueo en orden fijo (por clave primaria): dos reprogramaciones cruzadas no pueden interbloquearse.
            bloqueadas = {f.pk: f for f in Franja.objects.select_for_update().filter(pk__in=[t.franja_id, nueva_franja_id]).order_by("pk")}
            nueva = bloqueadas.get(nueva_franja_id)
            persona_prioritaria = cuentas.es_prioritaria(t.afiliado_id)
            if (nueva is None or nueva.pk == t.franja_id or nueva.estado != EstadoFranja.LIBRE or nueva.especialidad_id != t.especialidad_id
                    or nueva.inicio <= ahora or (nueva.cupo_prioritario and not persona_prioritaria)):
                codigo = "FRANJA_NO_DISPONIBLE"
            elif nueva.inicio > ahora + timedelta(days=P.turnos_dias_anticipacion):
                codigo = "FUERA_DE_VENTANA"
            else:
                anterior = bloqueadas[t.franja_id]
                try:
                    with transaction.atomic():
                        t.franja_id, t.inicio, t.fin = nueva.pk, nueva.inicio, nueva.fin
                        t.estado = EstadoTurno.PENDIENTE
                        t.confirmar_antes_de = None
                        t.reprogramaciones += 1
                        t.save(update_fields=["franja", "inicio", "fin", "estado", "confirmar_antes_de", "reprogramaciones", "actualizado"])
                        SolicitudReserva.objects.create(afiliado_id=t.afiliado_id, solicitante_id=solicitante.id, clave_idempotencia=clave_idempotencia, huella=huella, turno=t)
                except IntegrityError as error:
                    codigo = _codigo_integridad(error)
                else:
                    anterior.estado = EstadoFranja.LIBRE
                    anterior.save(update_fields=["estado"])
                    nueva.estado = EstadoFranja.OCUPADA
                    nueva.save(update_fields=["estado"])
                    _aud(Acciones.TURNO_REPROGRAMADO, solicitante, t, franja_anterior=str(anterior.pk), franja_nueva=str(nueva.pk), n=t.reprogramaciones)
    if codigo == "IDEMPOTENCIA":
        previa = SolicitudReserva.objects.filter(afiliado_id=t0.afiliado_id, clave_idempotencia=clave_idempotencia).select_related("turno").first()
        if previa is not None:
            vista, _ = _repetir(solicitante, previa, huella)
            return vista, False
    if codigo:
        _aud(Acciones.RESERVA_RECHAZADA, solicitante, t0, resultado=auditoria.DENEGADO, codigo=codigo)
        raise ReglaRechazada(codigo, "No fue posible reprogramar este turno. Puede intentar con otra franja o acudir a su unidad.", ofrecer_presencial=True)
    integraciones.cancelar_en_agenda(str(t.id))
    _avisar_representante(solicitante, t)
    _confirmar_en_agenda(t.id)
    t = _turno_con_relaciones().get(pk=t.id)
    return _vista_turno(t, solicitante, delegacion_vigente=vigente), True


def _a_uuid(valor):
    import uuid

    return uuid.UUID(str(valor))


# --- Inasistencias (§3.5) ---------------------------------------------------------------------------------------------------------------------


def _causas_automaticas():
    return list(CAUSAS)


def recalcular_restricciones(afiliado_id) -> None:
    """Recalcula desde el historial las restricciones automáticas de una persona (aviso, tope reducido, bloqueo).

    - 1+ inasistencias injustificadas en 90 días -> tope de 1 turno activo en total.
    - 2 inasistencias consecutivas en la misma especialidad -> bloqueo de la reserva en línea de ESA especialidad 14 días.
    - 3+ en 90 días -> revisión humana de la unidad (se audita; no hay sanción automática adicional).
    - Una inasistencia justificada se revierte. Los grupos de atención prioritaria quedan exentos (RT-11).
    Nunca bloquea la atención presencial: aquí solo se restringe la reserva en línea.
    """
    ahora = reloj.ahora()
    exento = cuentas.es_prioritaria(afiliado_id)
    ventana = ahora - timedelta(days=P.inasistencias_ventana_dias)
    faltas = list(Turno.objects.filter(afiliado_id=afiliado_id, estado=EstadoTurno.NO_ASISTIO, inasistencia_registrada_en__gte=ventana).order_by("-inasistencia_registrada_en"))
    deseadas: dict[tuple, tuple] = {}
    if faltas and not exento:
        ultima = faltas[0]
        deseadas[(TipoRestriccion.TOPE_REDUCIDO, None)] = (
            "INASISTENCIA_INJUSTIFICADA", ultima.inasistencia_registrada_en + timedelta(days=P.inasistencias_ventana_dias), ultima)
        for esp_id in {f.especialidad_id for f in faltas}:
            ultimos = list(
                Turno.objects.filter(afiliado_id=afiliado_id, especialidad_id=esp_id, estado__in=[EstadoTurno.ATENDIDO, EstadoTurno.NO_ASISTIO, EstadoTurno.JUSTIFICADO])
                .order_by("-inicio")[: P.inasistencias_consecutivas_para_bloqueo]
            )
            if len(ultimos) == P.inasistencias_consecutivas_para_bloqueo and all(u.estado == EstadoTurno.NO_ASISTIO for u in ultimos):
                fin = ultimos[0].inasistencia_registrada_en + timedelta(days=P.inasistencias_bloqueo_dias)
                deseadas[(TipoRestriccion.BLOQUEO_RESERVA, esp_id)] = ("INASISTENCIAS_CONSECUTIVAS", fin, ultimos[0])
    existentes = {(r.tipo, r.especialidad_id): r for r in Restriccion.objects.filter(afiliado_id=afiliado_id, revertida_en__isnull=True, causa__in=_causas_automaticas())}
    nuevas, levantadas = [], 0
    with transaction.atomic():
        for clave, (causa, fin, origen) in deseadas.items():
            if fin <= ahora:
                continue
            actual = existentes.pop(clave, None)
            if actual is None:
                r = Restriccion.objects.create(afiliado_id=afiliado_id, especialidad_id=clave[1], tipo=clave[0], causa=causa, inicio=ahora, fin=fin, turno_origen=origen)
                nuevas.append(r)
                auditoria.registrar(accion=Acciones.RESTRICCION_APLICADA, actor_id=afiliado_id, recurso_tipo="restriccion", recurso_id=r.id, detalle={"causa": causa, "fin": fin.isoformat()})
            else:
                Restriccion.objects.filter(pk=actual.pk).update(fin=fin, turno_origen=origen)
        for sobrante in existentes.values():  # ya no corresponde: justificada, vencida o exenta
            Restriccion.objects.filter(pk=sobrante.pk).update(revertida_en=ahora)
            levantadas += 1
            auditoria.registrar(accion=Acciones.RESTRICCION_LEVANTADA, actor_id=afiliado_id, recurso_tipo="restriccion", recurso_id=sobrante.id)
        if len(faltas) == P.inasistencias_revision_humana:
            auditoria.registrar(accion=Acciones.INASISTENCIAS_REVISION_HUMANA, actor_id=afiliado_id, recurso_tipo="usuario", recurso_id=afiliado_id,
                                detalle={"inasistencias": len(faltas)})
    for r in nuevas:
        if r.causa == "INASISTENCIAS_CONSECUTIVAS":
            consecuencia = f"La reserva en línea de esa especialidad queda bloqueada hasta el {_fmt_fecha(r.fin)}."
        else:
            consecuencia = f"Hasta el {_fmt_fecha(r.fin)} solo podrá tener {P.inasistencias_tope_reducido} cita activa."
        _notificar(afiliado_id, "inasistencia_registrada", consecuencia=consecuencia, horas=P.inasistencias_horas_para_justificar)
    if levantadas and not nuevas:
        _notificar(afiliado_id, "restriccion_levantada")


def registrar_atencion(funcionario, turno_id, resultado: str, *, justificacion_tipo: str = "") -> TurnoVista:
    """E16: recepción de la misma unidad registra ATENDIDO, NO_ASISTIO o la justificación (72 h). Idempotente y auditado.

    El afiliado NUNCA marca sus propias inasistencias. Una inasistencia solo se puede registrar cuando la hora ya llegó.
    """
    if resultado not in ("ATENDIDO", "NO_ASISTIO", "JUSTIFICADO"):
        raise ParametroInvalido("resultado")
    try:
        t = _turno_con_relaciones().filter(pk=turno_id).first()
    except (ValueError, TypeError, ValidationError):
        t = None
    if t is None:
        raise NoEncontrado()
    if not politicas.puede_registrar_atencion(funcionario, t):
        _aud(Acciones.ACCESO_DENEGADO, funcionario, t, resultado=auditoria.DENEGADO, accion_intentada="registrar_atencion")
        raise AccesoDenegado()
    cambio = None
    with transaction.atomic():
        t = _turno_con_relaciones().select_for_update(of=("self",)).get(pk=t.pk)
        ahora = reloj.ahora()
        previo = t.estado
        if previo == resultado:
            cambio = False  # idempotente
        elif resultado in ("ATENDIDO", "NO_ASISTIO"):
            if previo != EstadoTurno.CONFIRMADO or ahora < t.inicio:
                raise ReglaRechazada("NO_SE_PUEDE_REGISTRAR", "Solo se puede registrar la atención de un turno confirmado cuya hora ya llegó.")
            t.estado = resultado
            t.atencion_registrada_por_id = funcionario.id
            if resultado == "NO_ASISTIO":
                t.inasistencia_registrada_en = ahora
            t.save(update_fields=["estado", "atencion_registrada_por", "inasistencia_registrada_en", "actualizado"])
            cambio = True
        else:  # JUSTIFICADO
            if justificacion_tipo not in ("DOCUMENTO", "DECLARACION"):
                raise ParametroInvalido("justificacion_tipo")
            limite = (t.inasistencia_registrada_en or ahora) + timedelta(hours=P.inasistencias_horas_para_justificar)
            if previo != EstadoTurno.NO_ASISTIO or ahora > limite:
                raise ReglaRechazada("JUSTIFICACION_FUERA_DE_PLAZO", f"La justificación debe presentarse dentro de {P.inasistencias_horas_para_justificar} horas.")
            t.estado = EstadoTurno.JUSTIFICADO
            t.justificado_en = ahora
            t.justificacion_tipo = justificacion_tipo
            t.save(update_fields=["estado", "justificado_en", "justificacion_tipo", "actualizado"])
            cambio = True
        if cambio:
            accion = {"ATENDIDO": Acciones.ASISTENCIA_REGISTRADA, "NO_ASISTIO": Acciones.INASISTENCIA_REGISTRADA, "JUSTIFICADO": Acciones.INASISTENCIA_JUSTIFICADA}[resultado]
            _aud(accion, funcionario, t, estado_previo=previo, estado_nuevo=resultado, justificacion=justificacion_tipo)
    if cambio and resultado in ("NO_ASISTIO", "JUSTIFICADO"):
        recalcular_restricciones(t.afiliado_id)
    t = _turno_con_relaciones().get(pk=t.pk)
    return TurnoVista(
        id=t.id, estado=t.estado, estado_texto=ESTADO_TEXTO[t.estado], afiliado_id=t.afiliado_id, afiliado_nombre="", especialidad=t.especialidad.nombre,
        establecimiento=t.franja.establecimiento.nombre, inicio=t.inicio, fin=t.fin, confirmar_antes_de=t.confirmar_antes_de,
        reprogramaciones=t.reprogramaciones, reprogramaciones_restantes=0,
    )


def apelar_restriccion(solicitante, turno_id, texto: str) -> Apelacion:
    """E12: el titular apela ante una persona de la unidad (respuesta en 5 días hábiles, LOPDP art. 20). Texto limitado."""
    t, _ = _cargar_turno(solicitante, turno_id, para="ver")
    if not politicas.puede_apelar(solicitante, t.afiliado_id):
        _aud(Acciones.ACCESO_DENEGADO, solicitante, t, resultado=auditoria.DENEGADO, accion_intentada="apelar")
        raise NoEncontrado()
    texto = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", (texto or "")).strip()
    if not texto or len(texto) > P.apelacion_texto_maximo:
        raise ReglaRechazada("TEXTO_INVALIDO", f"Escriba entre 1 y {P.apelacion_texto_maximo} caracteres.")
    ahora = reloj.ahora()
    restriccion = t.restricciones_derivadas.filter(revertida_en__isnull=True, fin__gt=ahora).order_by("-fin").first()
    if restriccion is None:
        raise ReglaRechazada("SIN_RESTRICCION", "No hay una restricción vigente que apelar para este turno.")
    with transaction.atomic():
        existente = Apelacion.objects.filter(restriccion=restriccion, estado=EstadoApelacion.PENDIENTE).first()
        if existente is not None:
            return existente
        apelacion = Apelacion.objects.create(
            restriccion=restriccion, turno=t, afiliado_id=t.afiliado_id, texto=texto, creada=ahora, responder_antes_de=_dias_habiles(ahora, P.inasistencias_dias_habiles_apelacion)
        )
        _aud(Acciones.APELACION_CREADA, solicitante, t, apelacion_id=str(apelacion.id))
    _notificar(t.afiliado_id, "apelacion_recibida", fecha=zona.localtime(apelacion.responder_antes_de).strftime("%d/%m/%Y"))
    return apelacion


def resolver_apelacion(resolutor, apelacion_id, *, aceptar: bool, motivo: str = "") -> Apelacion:
    """Una persona de la unidad resuelve la apelación. Aceptada: se levanta la restricción."""
    if not politicas.puede_resolver_apelacion(resolutor):
        auditoria.registrar(accion=Acciones.ACCESO_DENEGADO, actor=resolutor, resultado=auditoria.DENEGADO, recurso_tipo="apelacion", recurso_id=apelacion_id)
        raise AccesoDenegado()
    with transaction.atomic():
        try:
            a = Apelacion.objects.select_for_update().select_related("restriccion").filter(pk=apelacion_id).first()
        except (ValueError, TypeError, ValidationError):
            a = None
        if a is None:
            raise NoEncontrado()
        if a.estado != EstadoApelacion.PENDIENTE:
            return a
        ahora = reloj.ahora()
        a.estado = EstadoApelacion.ACEPTADA if aceptar else EstadoApelacion.RECHAZADA
        a.resuelta_en, a.resuelta_por_id, a.motivo_resolucion = ahora, resolutor.id, (motivo or "")[:200]
        a.save(update_fields=["estado", "resuelta_en", "resuelta_por", "motivo_resolucion"])
        if aceptar:
            Restriccion.objects.filter(pk=a.restriccion_id, revertida_en__isnull=True).update(revertida_en=ahora)
        auditoria.registrar(accion=Acciones.APELACION_RESUELTA, actor=resolutor, recurso_tipo="apelacion", recurso_id=a.id, detalle={"aceptada": aceptar, "afiliado_id": str(a.afiliado_id)})
    _notificar(a.afiliado_id, "apelacion_resuelta")
    return a


# --- E17 / E18: lado interno --------------------------------------------------------------------------------------------------------------------


def agenda_unidad(funcionario, *, fecha: date | None = None, pagina=1, tamano=None) -> PaginaAgenda:
    """Agenda de la unidad del funcionario para un día: sin datos clínicos, paginada, sin exportación masiva."""
    pagina, tamano = _paginar(pagina, tamano)
    establecimiento_id = getattr(funcionario, "establecimiento_id", None)
    if not politicas.puede_ver_agenda_unidad(funcionario, establecimiento_id):
        auditoria.registrar(accion=Acciones.ACCESO_DENEGADO, actor=funcionario, resultado=auditoria.DENEGADO, recurso_tipo="agenda")
        raise AccesoDenegado()
    fecha = fecha or zona.localdate(reloj.ahora())
    inicio_dia = zona.make_aware(datetime.combine(fecha, time.min))
    consulta = _turno_con_relaciones().filter(
        franja__establecimiento_id=establecimiento_id, inicio__gte=inicio_dia, inicio__lt=inicio_dia + timedelta(days=1),
        estado__in=[*ESTADOS_ACTIVOS, EstadoTurno.ATENDIDO, EstadoTurno.NO_ASISTIO, EstadoTurno.JUSTIFICADO],
    )
    total = consulta.count()
    desde = (pagina - 1) * tamano
    turnos = list(consulta.order_by("inicio", "id")[desde : desde + tamano])
    personas = cuentas.personas_por_ids({t.afiliado_id for t in turnos})
    ahora = reloj.ahora()
    filas = []
    for t in turnos:
        p = personas.get(t.afiliado_id)
        filas.append(FilaAgenda(
            turno_id=t.id, inicio=t.inicio, especialidad=t.especialidad.nombre, estado=t.estado, estado_texto=ESTADO_TEXTO[t.estado],
            persona=p.nombre_completo if p else "", cedula_enmascarada=politicas.enmascarar_cedula(p.cedula) if p else "",
            puede_registrar=bool(politicas.puede_registrar_atencion(funcionario, t) and t.estado == EstadoTurno.CONFIRMADO and ahora >= t.inicio),
            justificable=bool(politicas.puede_registrar_atencion(funcionario, t) and t.estado == EstadoTurno.NO_ASISTIO),
        ))
    return PaginaAgenda(filas, fecha, pagina, tamano, total)


def consultar_afiliado(funcionario, cedula: str, justificacion: str = "") -> ContactoVista:
    """E18: contacto de un afiliado, ENMASCARADO; completo solo con justificación registrada (recepción).

    Recepción solo consulta personas con turnos en SU unidad (CA-09); soporte ve solo datos enmascarados. Máximo 30
    consultas por hora y por funcionario. Toda consulta queda auditada.
    """
    nivel = politicas.puede_consultar_contacto(funcionario)
    if not nivel:
        auditoria.registrar(accion=Acciones.ACCESO_DENEGADO, actor=funcionario, resultado=auditoria.DENEGADO, recurso_tipo="afiliado")
        raise AccesoDenegado()
    cuentas.limitar_por_cuenta("contacto", funcionario.id, P.consultas_contacto_por_hora_funcionario, 3600)
    contacto = cuentas.contacto_por_cedula(cedula)
    if contacto is None:
        raise NoEncontrado()
    if funcionario.rol == "RECEPCION":
        relacion = Turno.objects.filter(afiliado_id=contacto.persona_id, franja__establecimiento_id=funcionario.establecimiento_id).exists()
        if not relacion:
            auditoria.registrar(accion=Acciones.ACCESO_DENEGADO, actor=funcionario, resultado=auditoria.DENEGADO, recurso_tipo="afiliado",
                                recurso_id=contacto.persona_id, detalle={"motivo": "sin_relacion_con_la_unidad"})
            raise AccesoDenegado()
    justificacion = (justificacion or "").strip()
    valida = len(justificacion) >= P.justificacion_acceso_minimo_caracteres
    completo = politicas.puede_ver_contacto_completo(funcionario, justificacion_registrada=valida)
    auditoria.registrar(
        accion=Acciones.CONSULTA_CONTACTO, actor=funcionario, recurso_tipo="afiliado", recurso_id=contacto.persona_id,
        detalle={"completo": completo, "justificacion": justificacion[:200] if completo else ""},
    )
    if completo:
        return ContactoVista(f"{contacto.nombres} {contacto.apellidos}".strip(), contacto.cedula, contacto.correo, contacto.telefono, True)
    return ContactoVista(
        f"{contacto.nombres} {contacto.apellidos}".strip(), politicas.enmascarar_cedula(contacto.cedula),
        politicas.enmascarar_correo(contacto.correo), politicas.enmascarar_telefono(contacto.telefono), False,
    )


# --- Tareas programadas ----------------------------------------------------------------------------------------------------------------------------


def expirar_vencidos(lote: int = 200) -> dict:
    """RT-05: los turnos RESERVADO sin confirmar en 24 h EXPIRAN y liberan el cupo; un PENDIENTE sin confirmación de la agenda
    en `turnos_pendiente_horas_maximas` se cancela por la institución. Idempotente y apto para ejecutarse en paralelo."""
    ahora = reloj.ahora()
    expirados = cancelados = 0
    with transaction.atomic():
        vencidos = list(
            Turno.objects.select_for_update(skip_locked=True, of=("self",)).filter(estado=EstadoTurno.RESERVADO, confirmar_antes_de__lt=ahora)[:lote]
        )
        for t in vencidos:
            t.estado = EstadoTurno.EXPIRADO
            t.save(update_fields=["estado", "actualizado"])
            Franja.objects.filter(pk=t.franja_id).update(estado=EstadoFranja.LIBRE)
            _aud(Acciones.TURNO_EXPIRADO, None, t)
            expirados += 1
    for t in vencidos:
        integraciones.cancelar_en_agenda(str(t.id))
        _notificar(t.afiliado_id, "cita_expirada")
    limite = ahora - timedelta(hours=P.turnos_pendiente_horas_maximas)
    for turno_id in list(Turno.objects.filter(estado=EstadoTurno.PENDIENTE, creado__lt=limite).values_list("id", flat=True)[:lote]):
        _cancelar_por_institucion(turno_id, "SIN_CONFIRMACION_DE_AGENDA")
        cancelados += 1
    return {"expirados": expirados, "pendientes_cancelados": cancelados}


def conciliar_pendientes(lote: int = 200) -> dict:
    """Conciliación periódica con la agenda: reintenta confirmar los turnos PENDIENTE (afiliación o agenda estuvieron caídas)."""
    espera = reloj.ahora() - timedelta(seconds=5)
    ids = list(Turno.objects.filter(estado=EstadoTurno.PENDIENTE, actualizado__lt=espera).values_list("id", flat=True)[:lote])
    resultado = {"revisados": len(ids), "reservados": 0, "siguen_pendientes": 0, "cancelados": 0}
    for turno_id in ids:
        estado = _confirmar_en_agenda(turno_id)
        clave = {EstadoTurno.RESERVADO: "reservados", EstadoTurno.PENDIENTE: "siguen_pendientes"}.get(estado, "cancelados")
        resultado[clave] += 1
    return resultado


# --- Pantallas: un solo servicio por vista (la vista no combina consultas) -------------------------------------------------------------


@dataclass(frozen=True)
class PantallaDisponibilidad:
    pagina: PaginaDisponibilidad
    catalogos: dict
    representados: list
    restricciones: list
    puede_reservar: bool


def pantalla_disponibilidad(usuario, **filtros) -> PantallaDisponibilidad:
    pagina = buscar_disponibilidad(usuario=usuario, **filtros)
    puede = politicas.puede_reservar_en_linea(usuario)
    return PantallaDisponibilidad(
        pagina=pagina,
        catalogos=catalogos(),
        representados=delegaciones.representados_de(usuario) if puede else [],
        restricciones=restricciones_de(usuario) if puede else [],
        puede_reservar=puede,
    )


@dataclass(frozen=True)
class PantallaTurnos:
    pagina: PaginaTurnos
    restricciones: list
    representados: list
    afiliado_id: object


def pantalla_turnos(usuario, *, afiliado_id=None, estado=None, pagina=1, tamano=None) -> PantallaTurnos:
    lista = listar_turnos(usuario, afiliado_id=afiliado_id, estado=estado, pagina=pagina, tamano=tamano)
    objetivo = afiliado_id or usuario.id
    return PantallaTurnos(lista, restricciones_de(usuario, objetivo), delegaciones.representados_de(usuario), objetivo)

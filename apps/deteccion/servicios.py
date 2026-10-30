"""Detección y respuesta (Anexo I, reglas D01-D18).

FUENTE ÚNICA DE EVENTOS: este módulo solo LEE los eventos que ya registra `auditoria` (más los contadores de métricas de
solicitudes para D12/D13). No tiene vistas que lo alimenten ni importa vistas de otras apps. Cada regla recorre una
ventana deslizante, produce alertas con severidad y, cuando corresponde, dispara una acción automática (bloqueo temporal
del origen, desafío adaptativo, invalidar un token, sala de espera, exigir restablecer la clave).

Las alertas no llevan datos personales: solo referencias no reversibles, contadores y un prefijo de red. Los umbrales
salen de `config/parametros.py` (por ejemplo D01: 50 fallos en 10 minutos desde una misma IP).
"""
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone as zona

from apps.auditoria import servicios as auditoria
from apps.auditoria.servicios import Acciones
from apps.cuentas import politicas
from apps.cuentas import servicios as cuentas
from apps.cuentas.servicios import AccesoDenegado, NoEncontrado
from apps.notificaciones import servicios as notificaciones
from config import bloqueos, metricas, reloj
from config.parametros import P

from .modelos import Alerta, EstadoAlerta, Severidad

logger = logging.getLogger(__name__)

__all__ = ["AccesoDenegado", "NoEncontrado", "REGLAS", "evaluar", "listar_alertas", "reconocer_alerta", "resumen_panel"]

CRITICA, ALTA, MEDIA, INFORMATIVA = (s.value for s in (Severidad.CRITICA, Severidad.ALTA, Severidad.MEDIA, Severidad.INFORMATIVA))


@dataclass
class Hallazgo:
    regla: str
    severidad: str
    titulo: str
    clave: str
    detalle: dict = field(default_factory=dict)
    accion: str = ""  # texto de la acción automática (si la hay)
    ejecutar: Callable[[], object] | None = None
    vence_en: datetime | None = None


def _prefijo_ip(ip: str | None) -> str:
    """Prefijo de red para mostrar en el panel (nunca la IP completa)."""
    if not ip:
        return "desconocido"
    if ":" in ip:
        return ":".join(ip.split(":")[:3]) + "::/48"
    p = ip.split(".")
    return ".".join(p[:2]) + ".x.x" if len(p) == 4 else "desconocido"


def _ventana(ahora: datetime, minutos: float) -> tuple[datetime, int]:
    """(inicio de la ventana, número de ventana): el número sirve para no repetir la misma alerta por el mismo hecho."""
    return ahora - timedelta(minutes=minutos), int(ahora.timestamp() // max(1, minutos * 60))


def _identidad(evento: dict) -> str | None:
    """Referencia de la cuenta involucrada: la guardada por la auditoría (HMAC de la cédula) o el actor."""
    return (evento["detalle"] or {}).get("cedula_ref") or (str(evento["actor_id"]) if evento["actor_id"] else None)


# --- Reglas --------------------------------------------------------------------------------------------------------------------------------------


def d01_fuerza_bruta_por_ip(ahora):
    desde, n = _ventana(ahora, P.d01_ventana_minutos)
    por_ip = defaultdict(int)
    for e in auditoria.consultar_ventana([Acciones.LOGIN_FALLIDO, Acciones.LOGIN_BLOQUEADO], desde):
        if e["ip"]:
            por_ip[e["ip"]] += 1
    return [
        Hallazgo("D01", ALTA, "Fallos de inicio de sesión desde una misma IP", f"{ip}:{n}", {"origen": _prefijo_ip(ip), "fallos": cuenta},
                 f"Bloqueo temporal del origen {P.bloqueo_origen_minutos} min", lambda ip=ip: bloqueos.bloquear_origen(ip, P.bloqueo_origen_minutos * 60, "D01"))
        for ip, cuenta in por_ip.items() if cuenta >= P.d01_fallos_por_ip
    ]


def d02_relleno_de_credenciales(ahora):
    desde, n = _ventana(ahora, P.d02_ventana_minutos)
    cuentas_ = {_identidad(e) for e in auditoria.consultar_ventana([Acciones.LOGIN_FALLIDO], desde)} - {None}
    if len(cuentas_) < P.d02_cuentas_distintas:
        return []
    return [Hallazgo("D02", ALTA, "Relleno de credenciales distribuido: fallos en muchas cuentas", f"global:{n}", {"cuentas_distintas": len(cuentas_)},
                     f"Desafío adaptativo activado {P.desafio_minutos} min", lambda: bloqueos.activar_desafio(P.desafio_minutos * 60))]


def d03_cuenta_atacada_desde_muchas_ip(ahora):
    desde, n = _ventana(ahora, P.d03_ventana_minutos)
    fallos, ips = defaultdict(int), defaultdict(set)
    for e in auditoria.consultar_ventana([Acciones.LOGIN_FALLIDO], desde):
        ref = (e["detalle"] or {}).get("cedula_ref")
        if ref and e["ip"]:
            fallos[ref] += 1
            ips[ref].add(e["ip"])
    return [
        Hallazgo("D03", ALTA, "Una cuenta atacada desde muchos orígenes", f"{ref}:{n}", {"cuenta_ref": ref, "fallos": fallos[ref], "origenes": len(ips[ref])},
                 "Clave invalidada y aviso al titular: debe restablecerla", lambda ref=ref: cuentas.exigir_restablecer_clave_por_ref(ref))
        for ref in fallos if fallos[ref] >= P.d03_fallos_por_cuenta and len(ips[ref]) >= P.d03_ips_distintas
    ]


def d04_enumeracion_de_cedulas(ahora):
    desde, n = _ventana(ahora, P.d04_ventana_minutos)
    por_ip = defaultdict(set)
    for e in auditoria.consultar_ventana([Acciones.ACTIVACION_SOLICITADA, Acciones.RECUPERACION_SOLICITADA, Acciones.LOGIN_FALLIDO, Acciones.ACTIVACION_FALLIDA], desde):
        ref = _identidad(e)
        if ref and e["ip"]:
            por_ip[e["ip"]].add(ref)
    return [
        Hallazgo("D04", MEDIA, "Enumeración de cédulas desde una IP", f"{ip}:{n}", {"origen": _prefijo_ip(ip), "cedulas_distintas": len(refs)},
                 f"Bloqueo temporal del origen {P.bloqueo_origen_minutos} min", lambda ip=ip: bloqueos.bloquear_origen(ip, P.bloqueo_origen_minutos * 60, "D04"))
        for ip, refs in por_ip.items() if len(refs) >= P.d04_cedulas_distintas
    ]


def d05_dispositivo_nuevo(ahora):
    desde = ahora - timedelta(minutes=P.d06_ventana_ip_minutos)
    return [Hallazgo("D05", INFORMATIVA, "Inicio de sesión desde un dispositivo nuevo", f"ev:{e['secuencia']}", {"evento": e["secuencia"]})
            for e in auditoria.consultar_ventana([Acciones.DISPOSITIVO_NUEVO], desde)]


def d06_acaparamiento_de_turnos(ahora):
    desde, n = _ventana(ahora, 60)
    por_cuenta, cuentas_por_ip = defaultdict(int), defaultdict(set)
    for e in auditoria.consultar_ventana([Acciones.RESERVA_CREADA, Acciones.RESERVA_RECHAZADA], desde):
        if e["actor_id"]:
            por_cuenta[str(e["actor_id"])] += 1
            if e["ip"]:
                cuentas_por_ip[e["ip"]].add(str(e["actor_id"]))
    hallazgos = [Hallazgo("D06", MEDIA, "Muchos intentos de reserva en una hora desde una cuenta", f"cta:{c}:{n}", {"cuenta_ref": c, "intentos": k}, "Límite por cuenta y revisión")
                 for c, k in por_cuenta.items() if k > P.d06_intentos_por_hora_cuenta]
    hallazgos += [
        Hallazgo("D06", MEDIA, "Varias cuentas reservando desde una misma IP", f"ip:{ip}:{n}", {"origen": _prefijo_ip(ip), "cuentas": len(c)},
                 f"Origen limitado {P.d06_bloqueo_origen_segundos // 60} min y revisión", lambda ip=ip: bloqueos.bloquear_origen(ip, P.d06_bloqueo_origen_segundos, "D06"))
        for ip, c in cuentas_por_ip.items() if len(c) >= P.d06_cuentas_distintas_por_ip
    ]
    return hallazgos


def d07_sondeo_de_identificadores(ahora):
    desde, n = _ventana(ahora, P.d07_ventana_minutos)
    por_usuario = defaultdict(int)
    for e in auditoria.consultar_ventana([Acciones.ACCESO_DENEGADO], desde):
        if e["actor_id"]:
            por_usuario[str(e["actor_id"])] += 1
    return [Hallazgo("D07", MEDIA, "Sondeo de identificadores: muchas respuestas 403/404 a un usuario", f"{u}:{n}", {"cuenta_ref": u, "respuestas": k}, "Revisar la cuenta")
            for u, k in por_usuario.items() if k >= P.d07_respuestas_por_usuario]


def d08_consulta_masiva_por_funcionario(ahora):
    desde, n = _ventana(ahora, 60)
    fichas, fuera = defaultdict(set), defaultdict(int)
    for e in auditoria.consultar_ventana([Acciones.FICHA_CONSULTADA, Acciones.CONSULTA_CONTACTO], desde):
        if not e["actor_id"]:
            continue
        fichas[str(e["actor_id"])].add(e["recurso_id"])
        hora = zona.localtime(e["ocurrido_en"]).hour
        if not (P.horario_laboral_inicio <= hora < P.horario_laboral_fin):
            fuera[str(e["actor_id"])] += 1
    hallazgos = [Hallazgo("D08", ALTA, "Consulta masiva de fichas por un funcionario", f"masiva:{a}:{n}", {"funcionario_ref": a, "fichas": len(f)}, "Alerta al responsable")
                 for a, f in fichas.items() if len(f) >= P.d08_fichas_por_hora]
    dia = zona.localdate(ahora).isoformat()
    hallazgos += [Hallazgo("D08", ALTA, "Consultas de fichas fuera del horario laboral", f"fuera:{a}:{dia}", {"funcionario_ref": a, "consultas": k}, "Alerta al responsable")
                  for a, k in fuera.items()]
    return hallazgos


def d09_exportaciones(ahora):
    desde = ahora - timedelta(hours=24)
    hallazgos = []
    for e in auditoria.consultar_ventana([Acciones.EXPORTACION_ENTREGADA], desde):
        aprobada = bool((e["detalle"] or {}).get("aprobada"))
        hallazgos.append(Hallazgo("D09", MEDIA if aprobada else CRITICA, "Exportación de registros" if aprobada else "Exportación SIN doble aprobación",
                                  f"ev:{e['secuencia']}", {"evento": e["secuencia"], "aprobada": aprobada}, "Aviso" if aprobada else "Alerta crítica"))
    return hallazgos


def d10_acceso_de_emergencia(ahora):
    desde = ahora - timedelta(days=1)
    return [Hallazgo("D10", ALTA, "Acceso excepcional (de emergencia) a datos de un afiliado", f"ev:{e['secuencia']}", {"evento": e["secuencia"]},
                     "Aviso inmediato al responsable; revisión posterior", vence_en=ahora + timedelta(days=P.d10_revision_dias))
            for e in auditoria.consultar_ventana([Acciones.ACCESO_EMERGENCIA], desde)]


def d11_integridad_de_la_auditoria(ahora):
    desde = ahora - timedelta(days=1)
    return [Hallazgo("D11", CRITICA, "La verificación de la cadena de auditoría detectó una alteración", f"ev:{e['secuencia']}", dict(e["detalle"] or {}), "Abrir incidente")
            for e in auditoria.consultar_ventana([Acciones.VERIFICACION_AUDITORIA_FALLIDA], desde)]


def d12_errores_5xx(ahora):
    r = metricas.resumen(P.metricas_ventana_segundos)
    if r["total"] < P.d12_muestra_minima or r["porcentaje_5xx"] <= P.d12_porcentaje_5xx:
        return []
    n = int(ahora.timestamp() // P.metricas_ventana_segundos)
    return [Hallazgo("D12", ALTA, "Más de 2 % de errores 5xx en 5 minutos", f"g:{n}", {"porcentaje_5xx": round(r["porcentaje_5xx"], 1), "solicitudes": r["total"]}, "Alerta de disponibilidad")]


def d13_latencia(ahora):
    r = metricas.resumen(P.d13_ventana_minutos * 60)
    if r["total"] < P.d12_muestra_minima or r["p95"] <= P.d13_p95_segundos:
        return []
    n = int(ahora.timestamp() // (P.d13_ventana_minutos * 60))
    return [Hallazgo("D13", MEDIA, "Latencia p95 por encima del umbral", f"g:{n}", {"p95_s": r["p95"], "solicitudes": r["total"]},
                     f"Sala de espera activada {P.sala_espera_minutos} min", lambda: bloqueos.activar_sala_espera(P.sala_espera_minutos * 60))]


def d14_fallos_de_terceros(ahora):
    eventos = auditoria.consultar_ventana([Acciones.AFILIACION_NO_DISPONIBLE, Acciones.AGENDA_NO_DISPONIBLE], ahora - timedelta(minutes=P.d14_ventana_minutos))
    tiempos = [e["ocurrido_en"] for e in eventos]
    ancho = timedelta(seconds=P.d14_ventana_segundos)
    for i in range(len(tiempos)):
        j = i
        while j < len(tiempos) and tiempos[j] - tiempos[i] <= ancho:
            j += 1
        if j - i >= P.d14_fallos_terceros:
            return [Hallazgo("D14", MEDIA, "Fallos repetidos del servicio de afiliación o de agenda", f"t:{int(tiempos[i].timestamp() // 600)}", {"fallos": j - i},
                             "Circuito abierto y turnos en estado PENDIENTE")]
    return []


def d15_respaldos(ahora):
    hallazgos = [Hallazgo("D15", ALTA, "Falló la copia de respaldo", f"ev:{e['secuencia']}", dict(e["detalle"] or {}), "Corregir antes del siguiente ciclo")
                 for e in auditoria.consultar_ventana([Acciones.RESPALDO_FALLIDO], ahora - timedelta(days=2))]
    # Solo exige la prueba si el sistema ya lleva más de 90 días haciendo respaldos (el primero es anterior al plazo).
    hubo_respaldos = auditoria.consultar_ventana([Acciones.RESPALDO_REALIZADO], ahora - timedelta(days=P.d15_historial_respaldos_dias), hasta=ahora - timedelta(days=P.d15_dias_sin_prueba_restauracion), limite=1)
    if hubo_respaldos:
        probada = auditoria.consultar_ventana([Acciones.RESTAURACION_PROBADA], ahora - timedelta(days=P.d15_dias_sin_prueba_restauracion), limite=1)
        if not probada:
            hallazgos.append(Hallazgo("D15", ALTA, f"Sin prueba de restauración en {P.d15_dias_sin_prueba_restauracion} días", f"restauracion:{ahora.date().isoformat()}", {}, "Probar la restauración"))
    return hallazgos


def d16_vulnerabilidades(ahora):
    return [
        Hallazgo("D16", ALTA, "CVE crítico o del catálogo KEV en una dependencia", f"ev:{e['secuencia']}", dict(e["detalle"] or {}), f"Ticket con plazo de {P.d16_plazo_horas} h",
                 vence_en=e["ocurrido_en"] + timedelta(hours=P.d16_plazo_horas))
        for e in auditoria.consultar_ventana([Acciones.VULNERABILIDAD_DETECTADA], ahora - timedelta(days=P.d16_ventana_dias)) if (e["detalle"] or {}).get("severidad") in ("CRITICA", "KEV")
    ]


def d17_token_desde_varios_origenes(ahora):
    por_token = defaultdict(set)
    for e in auditoria.consultar_ventana([Acciones.ENLACE_USADO, Acciones.ENLACE_RECHAZADO, Acciones.RECUPERACION_FALLIDA, Acciones.RECUPERACION_COMPLETADA], ahora - timedelta(hours=24)):
        ref = (e["detalle"] or {}).get("token_ref")
        if ref and e["ip"]:
            por_token[ref].add(e["ip"])

    def invalidar(ref):
        return cuentas.invalidar_token_recuperacion_por_ref(ref) + notificaciones.invalidar_enlace_por_ref(ref)

    return [Hallazgo("D17", MEDIA, "El mismo token se presentó desde orígenes distintos", f"tok:{ref}", {"token_ref": ref, "origenes": len(ips)},
                     "Token invalidado y aviso al titular", lambda ref=ref: invalidar(ref))
            for ref, ips in por_token.items() if len(ips) >= 2]


def d18_cambios_de_privilegios(ahora):
    return [Hallazgo("D18", MEDIA, "Alta de un funcionario o cambio de rol", f"ev:{e['secuencia']}", dict(e["detalle"] or {}), "Aviso al responsable de seguridad")
            for e in auditoria.consultar_ventana([Acciones.CAMBIO_PRIVILEGIOS], ahora - timedelta(days=1))]


REGLAS: dict[str, Callable] = {
    "D01": d01_fuerza_bruta_por_ip, "D02": d02_relleno_de_credenciales, "D03": d03_cuenta_atacada_desde_muchas_ip, "D04": d04_enumeracion_de_cedulas,
    "D05": d05_dispositivo_nuevo, "D06": d06_acaparamiento_de_turnos, "D07": d07_sondeo_de_identificadores, "D08": d08_consulta_masiva_por_funcionario,
    "D09": d09_exportaciones, "D10": d10_acceso_de_emergencia, "D11": d11_integridad_de_la_auditoria, "D12": d12_errores_5xx, "D13": d13_latencia,
    "D14": d14_fallos_de_terceros, "D15": d15_respaldos, "D16": d16_vulnerabilidades, "D17": d17_token_desde_varios_origenes, "D18": d18_cambios_de_privilegios,
}


# --- Evaluación y alertas -----------------------------------------------------------------------------------------------------------------------


def _registrar(h: Hallazgo, ahora: datetime) -> Alerta | None:
    """Crea la alerta si es nueva (misma regla y misma clave = mismo hecho) y ejecuta su acción automática UNA vez."""
    try:
        with transaction.atomic():
            alerta, nueva = Alerta.objects.get_or_create(
                regla=h.regla, clave=h.clave[:120],
                defaults={"severidad": h.severidad, "titulo": h.titulo[:120], "detalle": h.detalle, "accion_automatica": h.accion[:200], "creada": ahora, "vence_en": h.vence_en},
            )
    except IntegrityError:
        return None
    if not nueva:
        return None
    auditoria.registrar(accion=Acciones.ALERTA_GENERADA, recurso_tipo="alerta", recurso_id=alerta.id, detalle={"regla": h.regla, "severidad": h.severidad})
    if h.ejecutar is not None:
        try:
            h.ejecutar()
            auditoria.registrar(accion=Acciones.ACCION_AUTOMATICA, recurso_tipo="alerta", recurso_id=alerta.id, detalle={"regla": h.regla, "accion": h.accion})
        except Exception as error:  # noqa: BLE001  (una acción fallida no debe ocultar la alerta)
            logger.error("acción automática de %s falló (%s)", h.regla, type(error).__name__)
    if h.severidad in (CRITICA, ALTA):
        for correo in cuentas.contactos_de_responsables():
            notificaciones.enviar(plantilla="emergencia_acceso" if h.regla == "D10" else "alerta_seguridad_interna", canal="CORREO", destino=correo)
    return alerta


def evaluar(ahora: datetime | None = None, reglas=None) -> list[Alerta]:
    """Evalúa las reglas (todas por defecto) y devuelve las alertas NUEVAS. Una regla que falla no detiene a las demás."""
    ahora = ahora or reloj.ahora()
    nuevas = []
    for id_, regla in REGLAS.items():
        if reglas and id_ not in reglas:
            continue
        try:
            hallazgos = regla(ahora)
        except Exception as error:  # noqa: BLE001
            logger.error("la regla %s falló (%s)", id_, type(error).__name__)
            continue
        for h in hallazgos:
            alerta = _registrar(h, ahora)
            if alerta is not None:
                nuevas.append(alerta)
    return nuevas


# --- Panel de alertas (responsable de seguridad) ------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class AlertaVista:
    id: object
    regla: str
    severidad: str
    severidad_texto: str
    titulo: str
    detalle: dict
    accion_automatica: str
    estado: str
    creada: datetime
    vence_en: datetime | None
    nota: str


@dataclass(frozen=True)
class PanelAlertas:
    alertas: list
    pagina: int
    tamano: int
    total: int
    abiertas_por_severidad: dict

    @property
    def hay_siguiente(self) -> bool:
        return self.pagina * self.tamano < self.total


_ORDEN = {CRITICA: 0, ALTA: 1, MEDIA: 2, INFORMATIVA: 3}


def _exigir(usuario):
    if not politicas.puede_gestionar_alertas(usuario):
        auditoria.registrar(accion=Acciones.ACCESO_DENEGADO, actor=usuario, resultado=auditoria.DENEGADO, recurso_tipo="alerta")
        raise AccesoDenegado()


def resumen_panel() -> dict:
    return {s: Alerta.objects.filter(estado=EstadoAlerta.ABIERTA, severidad=s).count() for s in _ORDEN}


def listar_alertas(usuario, *, severidad=None, estado=None, pagina=1, tamano=None) -> PanelAlertas:
    _exigir(usuario)
    tamano = max(1, min(tamano or P.paginacion_por_defecto, P.paginacion_maxima))
    pagina = max(1, pagina)
    consulta = Alerta.objects.all()
    if severidad in _ORDEN:
        consulta = consulta.filter(severidad=severidad)
    if estado in (EstadoAlerta.ABIERTA, EstadoAlerta.RECONOCIDA):
        consulta = consulta.filter(estado=estado)
    total = consulta.count()
    filas = sorted(consulta.order_by("-creada")[(pagina - 1) * tamano : pagina * tamano], key=lambda a: (_ORDEN[a.severidad], -a.creada.timestamp()))
    vistas = [AlertaVista(a.id, a.regla, a.severidad, Severidad(a.severidad).label, a.titulo, a.detalle, a.accion_automatica, a.estado, a.creada, a.vence_en, a.nota) for a in filas]
    return PanelAlertas(vistas, pagina, tamano, total, resumen_panel())


def reconocer_alerta(usuario, alerta_id, nota: str = "") -> AlertaVista:
    """Marca la alerta como atendida (revisión posterior de un acceso de emergencia, cierre de un incidente...). Idempotente."""
    _exigir(usuario)
    with transaction.atomic():
        try:
            a = Alerta.objects.select_for_update().filter(pk=alerta_id).first()
        except (ValueError, TypeError, ValidationError):
            a = None
        if a is None:
            raise NoEncontrado()
        if a.estado != EstadoAlerta.RECONOCIDA:
            a.estado, a.reconocida_en, a.reconocida_por, a.nota = EstadoAlerta.RECONOCIDA, reloj.ahora(), usuario.id, (nota or "").strip()[:300]
            a.save(update_fields=["estado", "reconocida_en", "reconocida_por", "nota"])
            auditoria.registrar(accion=Acciones.ALERTA_RECONOCIDA, actor=usuario, recurso_tipo="alerta", recurso_id=a.id, detalle={"regla": a.regla})
    return AlertaVista(a.id, a.regla, a.severidad, Severidad(a.severidad).label, a.titulo, a.detalle, a.accion_automatica, a.estado, a.creada, a.vence_en, a.nota)

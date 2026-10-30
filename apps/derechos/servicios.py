"""Derechos del titular, retención de datos y exportaciones puntuales (LOPDP arts. 12-16, 10 literal i; N03, N06, T10, T11).

La lógica vive aquí; las vistas solo llaman a una función. Orquesta a `cuentas`, `turnos`, `delegaciones` y
`notificaciones` mediante sus `servicios.py` públicos.
"""
import csv
import io
import json
import logging
from datetime import datetime, timedelta

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.auditoria import servicios as auditoria
from apps.auditoria.servicios import Acciones
from apps.cuentas import politicas
from apps.cuentas import servicios as cuentas
from apps.cuentas.servicios import AccesoDenegado, NoEncontrado, OperacionDiferida, ReautenticacionRequerida  # noqa: F401
from apps.delegaciones import servicios as delegaciones
from apps.notificaciones import servicios as notificaciones
from apps.turnos import servicios as turnos
from config import reloj
from config.parametros import P

from .modelos import ConstanciaVencimiento, OrdenEncargado, SolicitudExportacion

logger = logging.getLogger(__name__)
ENCARGADOS = ("PROVEEDOR_MENSAJERIA", "PROVEEDOR_HOSTING")  # encargados SIMULADOS (contrato de encargo, N06)


class DerechosRechazado(Exception):
    def __init__(self, codigo: str, mensaje: str):
        super().__init__(codigo)
        self.codigo, self.mensaje = codigo, mensaje


# --- Acceso: exportación en formato legible por máquina ----------------------------------------------------------------------------------


def exportar_mis_datos(request) -> str:
    """Derecho de acceso: todos los datos del titular en JSON (operación sensible: segundo factor reciente)."""
    usuario = request.user
    if not politicas.puede_solicitar_derechos(usuario):
        raise AccesoDenegado()
    cuentas.exigir_operacion_sensible(request)
    otorgadas, recibidas = delegaciones.listar_delegaciones(usuario)
    paquete = {
        "generado_en": reloj.ahora().isoformat(),
        "formato": "iess-turnos/datos-del-titular/v1",
        "perfil": cuentas.exportar_perfil(usuario),
        "turnos_y_restricciones": turnos.historial_de(usuario.id),
        "delegaciones_otorgadas": [{"representante": d.representante_nombre, "vence": d.vence.isoformat(), "tipo": d.tipo} for d in otorgadas],
        "delegaciones_recibidas": [{"titular": d.titular_nombre, "vence": d.vence.isoformat(), "tipo": d.tipo} for d in recibidas],
        "solicitudes_de_derechos": [
            {"tipo": s.tipo, "estado": s.estado, "creada": s.creada.isoformat(), "atender_antes_de": s.atender_antes_de.isoformat()}
            for s in cuentas.listar_solicitudes_derechos(usuario)
        ],
    }
    auditoria.registrar(accion=Acciones.DATOS_EXPORTADOS, actor=usuario, recurso_tipo="usuario", recurso_id=usuario.id)
    return json.dumps(paquete, ensure_ascii=False, indent=2, sort_keys=True)


# --- Atención de solicitudes por el delegado de protección de datos ------------------------------------------------------------------------


def listar_solicitudes(dpd, pagina: int = 1):
    return cuentas.listar_solicitudes_pendientes(dpd, pagina)


def atender_solicitud(dpd, solicitud_id) -> str:
    """El DPD atiende una solicitud dentro del plazo. Devuelve el resultado en texto. Idempotente."""
    titular_id, tipo, estado, id_ = cuentas.obtener_solicitud_para_atender(dpd, solicitud_id)
    if estado == "ATENDIDA":
        return "Ya estaba atendida."
    if tipo == "ACCESO":
        resultado = "El titular puede descargar sus datos en formato legible por máquina desde «Mis datos»."
    elif tipo == "RECTIFICACION":
        resultado = "El titular puede rectificar su contacto desde «Mis datos» (con confirmación en el contacto nuevo)."
    elif tipo == "OPOSICION":
        cuentas.aplicar_oposicion(titular_id)
        resultado = "Oposición aplicada: se retiró el consentimiento de SMS y se detuvieron los recordatorios."
    elif tipo == "ELIMINACION":
        resultado = _eliminar_titular(titular_id, id_)
    else:  # el esquema de la base ya lo impide
        raise DerechosRechazado("TIPO_DESCONOCIDO", "Tipo de solicitud no válido.")
    cuentas.marcar_solicitud_atendida(dpd, id_, resultado)
    auditoria.registrar(accion=Acciones.DERECHOS_ATENDIDO, actor=dpd, recurso_tipo="solicitud_derechos", recurso_id=id_, detalle={"tipo": tipo})
    return resultado


def _eliminar_titular(titular_id, solicitud_id) -> str:
    """Eliminación: cancela turnos activos, revoca delegaciones, anonimiza historial y cuenta y ORDENA a los encargados (3 y 5 días)."""
    anonimo = cuentas.asegurar_anonimo()
    n_turnos = turnos.retirar_agenda_de(titular_id, anonimo, motivo="ELIMINACION_A_PEDIDO")
    delegaciones.revocar_todas_de(titular_id)
    cuentas.anonimizar_cuenta(titular_id, motivo="ELIMINACION_A_PEDIDO")
    ahora = reloj.ahora()
    with transaction.atomic():
        for encargado in ENCARGADOS:
            OrdenEncargado.objects.get_or_create(solicitud_id=solicitud_id, encargado=encargado, etapa="NOTIFICAR", defaults={"vence": ahora + timedelta(days=P.encargado_plazo_notificacion_dias)})
            OrdenEncargado.objects.get_or_create(solicitud_id=solicitud_id, encargado=encargado, etapa="ELIMINAR", defaults={"vence": ahora + timedelta(days=P.encargado_plazo_eliminacion_dias)})
        auditoria.registrar(accion=Acciones.ORDEN_ENCARGADO, recurso_tipo="solicitud_derechos", recurso_id=solicitud_id, detalle={"encargados": list(ENCARGADOS)})
    return f"Cuenta eliminada: {n_turnos} turno(s) anonimizados y encargados notificados (3 y 5 días)."


def procesar_ordenes_encargados(ahora: datetime | None = None) -> dict:
    """Encargados SIMULADOS: confirman las órdenes pendientes. Lo que pasa de su plazo se reporta como vencido (para auditoría)."""
    ahora = ahora or reloj.ahora()
    cumplidas = vencidas = 0
    for orden in OrdenEncargado.objects.filter(estado="PENDIENTE"):
        if ahora - timedelta(days=P.encargado_plazo_eliminacion_dias) > orden.vence:
            vencidas += 1
            continue
        orden.estado, orden.cumplida_en = "CUMPLIDA", ahora
        orden.save(update_fields=["estado", "cumplida_en"])
        cumplidas += 1
    return {"cumplidas": cumplidas, "vencidas": vencidas}


# --- Vencimiento de datos (sección 2.2) -------------------------------------------------------------------------------------------------


def _constancia(categoria: str, accion: str, cantidad: int, ahora, corte) -> ConstanciaVencimiento:
    return ConstanciaVencimiento.objects.create(categoria=categoria, accion=accion, cantidad=cantidad, ejecutada_en=ahora, corte=corte)


def ejecutar_vencimiento(ahora: datetime | None = None, *, archivo_auditoria: str | None = None) -> list[ConstanciaVencimiento]:
    """Aplica los plazos de conservación y deja CONSTANCIA de cada categoría (eliminación, anonimización o archivo).

    Cuentas inactivas 24 meses -> anonimización; turnos y restricciones 12 meses -> agregado anónimo / eliminación; delegaciones
    12 meses tras vencer; notificaciones 6 meses; credenciales vencidas; auditoría 12 meses en línea -> archivo (no se borra).
    Idempotente: una segunda ejecución no encuentra nada que vencer.
    """
    ahora = ahora or reloj.ahora()
    constancias = []
    anonimo = cuentas.asegurar_anonimo()

    inactivas = cuentas.cuentas_inactivas(P.retencion_cuenta_inactiva_meses)
    n = 0
    for persona_id in inactivas:
        turnos.retirar_agenda_de(persona_id, anonimo, motivo="INACTIVIDAD")
        delegaciones.revocar_todas_de(persona_id)
        n += cuentas.anonimizar_cuenta(persona_id, motivo="INACTIVIDAD_24_MESES")
    constancias.append(_constancia("cuentas_inactivas", "ANONIMIZACION", n, ahora, ahora - timedelta(days=30 * P.retencion_cuenta_inactiva_meses)))

    constancias.append(_constancia("turnos", "ANONIMIZACION", turnos.anonimizar_turnos_vencidos(P.retencion_turnos_meses, anonimo), ahora, ahora - timedelta(days=30 * P.retencion_turnos_meses)))
    constancias.append(_constancia("restricciones", "ELIMINACION", turnos.purgar_restricciones_vencidas(P.retencion_restricciones_meses), ahora, ahora - timedelta(days=30 * P.retencion_restricciones_meses)))
    constancias.append(_constancia("delegaciones", "ELIMINACION", delegaciones.purgar_vencidas(P.retencion_delegaciones_meses), ahora, ahora - timedelta(days=30 * P.retencion_delegaciones_meses)))
    constancias.append(_constancia("notificaciones", "ELIMINACION", notificaciones.purgar_antiguas(P.notificaciones_conservacion_meses), ahora, ahora - timedelta(days=30 * P.notificaciones_conservacion_meses)))
    constancias.append(_constancia("credenciales_vencidas", "ELIMINACION", cuentas.purgar_credenciales_vencidas(P.retencion_codigos_dias), ahora, ahora - timedelta(days=P.retencion_codigos_dias)))
    if archivo_auditoria:
        corte = ahora - timedelta(days=30 * P.auditoria_meses_en_linea)
        constancias.append(_constancia("auditoria", "ARCHIVO", auditoria.archivar_antiguos(corte, archivo_auditoria), ahora, corte))

    auditoria.registrar(accion=Acciones.VENCIMIENTO_EJECUTADO, recurso_tipo="retencion", detalle={c.categoria: c.cantidad for c in constancias})
    return constancias


# --- Exportaciones puntuales con doble aprobación -----------------------------------------------------------------------------------------


def _exportacion(exportacion_id) -> SolicitudExportacion:
    try:
        e = SolicitudExportacion.objects.select_for_update().filter(pk=exportacion_id).first()
    except (ValueError, TypeError, ValidationError):
        e = None
    if e is None:
        raise NoEncontrado()
    return e


def solicitar_exportacion(usuario, *, motivo: str, desde: datetime, hasta: datetime) -> SolicitudExportacion:
    """Pide exportar eventos de auditoría de un rango. NO existe exportación masiva: tope de filas, motivo escrito y aprobación ajena."""
    if not politicas.puede_solicitar_exportacion(usuario):
        auditoria.registrar(accion=Acciones.ACCESO_DENEGADO, actor=usuario, resultado=auditoria.DENEGADO, recurso_tipo="exportacion")
        raise AccesoDenegado()
    motivo = (motivo or "").strip()
    if len(motivo) < 10 or hasta <= desde or hasta - desde > timedelta(days=31):
        raise DerechosRechazado("EXPORTACION_INVALIDA", "Indique el motivo (mínimo 10 caracteres) y un rango de hasta 31 días.")
    with transaction.atomic():
        e = SolicitudExportacion.objects.create(solicitante_id=usuario.id, alcance="AUDITORIA", motivo=motivo[:200], desde=desde, hasta=hasta, creada=reloj.ahora())
        auditoria.registrar(accion=Acciones.EXPORTACION_SOLICITADA, actor=usuario, recurso_tipo="exportacion", recurso_id=e.id, detalle={"alcance": "AUDITORIA"})
    for correo in cuentas.contactos_de_responsables():
        notificaciones.enviar(plantilla="exportacion_pendiente", canal="CORREO", destino=correo)
    return e


def aprobar_exportacion(usuario, exportacion_id) -> SolicitudExportacion:
    """La aprueba una SEGUNDA persona (DPD o TI); quien la pidió jamás puede aprobarla (también lo impide la base)."""
    with transaction.atomic():
        e = _exportacion(exportacion_id)
        if not politicas.puede_aprobar_exportacion(usuario, e.solicitante_id):
            auditoria.registrar(accion=Acciones.ACCESO_DENEGADO, actor=usuario, resultado=auditoria.DENEGADO, recurso_tipo="exportacion", recurso_id=e.id, detalle={"intento": "aprobar"})
            raise AccesoDenegado()
        if e.estado == "SOLICITADA":
            e.estado, e.aprobador_id, e.aprobada_en = "APROBADA", usuario.id, reloj.ahora()
            e.save(update_fields=["estado", "aprobador", "aprobada_en"])
            auditoria.registrar(accion=Acciones.EXPORTACION_APROBADA, actor=usuario, recurso_tipo="exportacion", recurso_id=e.id, detalle={"solicitante_id": str(e.solicitante_id)})
    return e


def entregar_exportacion(usuario, exportacion_id) -> str:
    """Entrega el CSV UNA sola vez, al solicitante, dentro de la vigencia de la aprobación. Sin secretos ni detalles."""
    with transaction.atomic():
        e = _exportacion(exportacion_id)
        if e.solicitante_id != usuario.id or not politicas.puede_solicitar_exportacion(usuario):
            auditoria.registrar(accion=Acciones.ACCESO_DENEGADO, actor=usuario, resultado=auditoria.DENEGADO, recurso_tipo="exportacion", recurso_id=e.id, detalle={"intento": "descargar"})
            raise AccesoDenegado()
        ahora = reloj.ahora()
        if e.estado != "APROBADA" or e.aprobador_id is None:
            raise DerechosRechazado("NO_APROBADA", "La exportación necesita la aprobación de una segunda persona antes de descargarse.")
        if ahora > e.aprobada_en + timedelta(hours=P.exportacion_vigencia_horas):
            e.estado = "VENCIDA"
            e.save(update_fields=["estado"])
            raise DerechosRechazado("VENCIDA", "La aprobación venció: solicite la exportación de nuevo.")
        eventos = auditoria.consultar_ventana(None, e.desde, hasta=e.hasta, limite=P.exportacion_filas_maximas)
        salida = io.StringIO()
        escritor = csv.writer(salida)
        escritor.writerow(["secuencia", "ocurrido_en", "actor_rol", "accion", "resultado", "recurso_tipo", "recurso_id"])
        for ev in eventos:
            escritor.writerow([ev["secuencia"], ev["ocurrido_en"].isoformat(), ev["actor_rol"], ev["accion"], ev["resultado"], ev["recurso_tipo"], ev["recurso_id"]])
        e.estado, e.entregada_en, e.filas = "ENTREGADA", ahora, len(eventos)
        e.save(update_fields=["estado", "entregada_en", "filas"])
        auditoria.registrar(accion=Acciones.EXPORTACION_ENTREGADA, actor=usuario, recurso_tipo="exportacion", recurso_id=e.id,
                            detalle={"aprobada": True, "aprobador_id": str(e.aprobador_id), "filas": len(eventos)})
    return salida.getvalue()


def listar_exportaciones(usuario) -> list[dict]:
    if not (politicas.puede_solicitar_exportacion(usuario) or politicas.puede_aprobar_exportacion(usuario, None)):
        raise AccesoDenegado()
    return [
        {"id": e.id, "motivo": e.motivo, "estado": e.estado, "creada": e.creada, "desde": e.desde, "hasta": e.hasta,
         "puede_aprobar": e.estado == "SOLICITADA" and politicas.puede_aprobar_exportacion(usuario, e.solicitante_id),
         "puede_descargar": e.estado == "APROBADA" and e.solicitante_id == usuario.id}
        for e in SolicitudExportacion.objects.order_by("-creada")[:50]
    ]

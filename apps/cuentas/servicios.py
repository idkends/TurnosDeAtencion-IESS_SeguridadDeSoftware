"""Servicios de identidad (Anexos E y F): activación, claves, inicio de sesión, segundo factor, sesiones y recuperación.

Toda operación que cambia estado vive aquí, dentro de transacciones, y es el único camino de escritura hacia el ORM.
Los permisos se deciden con `cuentas.politicas`; la auditoría, con `auditoria.registrar`.

Principios que recorren el módulo:
- Mensajes y tiempos idénticos exista o no la cédula (CWE-204/640): ver `_tiempo_minimo` y `CredencialesInvalidas`.
- El contacto de activación y de recuperación es SIEMPRE el registrado en el servicio de afiliación (anti pre-secuestro).
- Los secretos (códigos, tokens) se guardan solo como resumen; el secreto TOTP, cifrado.
"""
import hashlib
import hmac
import secrets
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta

import pyotp
import segno
from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.contrib.auth import login as django_login
from django.contrib.auth import logout as django_logout
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.sessions.models import Session
from django.core.cache import caches
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone as zona
from django.utils.safestring import mark_safe

from apps.auditoria import servicios as auditoria
from apps.auditoria.servicios import Acciones
from apps.integraciones import servicios as integraciones
from apps.notificaciones import servicios as notificaciones
from config import contexto, limites, reloj
from config.cedula import es_valida
from config.parametros import P

from . import claves, politicas
from .modelos import (
    CodigoActivacion,
    CodigoRecuperacionMfa,
    SesionDispositivo,
    TokenRecuperacion,
    Usuario,
)
from .roles import EstadoCuenta, Rol

# --- Excepciones de dominio (las vistas las traducen a mensajes genéricos) -----------------------------------------


class CredencialesInvalidas(Exception):
    """Cédula inexistente, clave incorrecta, cuenta bloqueada o inactiva: siempre el mismo error (CA-05)."""


class LimiteExcedido(Exception):
    def __init__(self, reintentar_en: int):
        super().__init__("límite de solicitudes excedido")
        self.reintentar_en = reintentar_en


class CodigoInvalido(Exception):
    """Código de activación o de segundo factor inválido, vencido o agotado."""


class TokenInvalido(Exception):
    """Token de recuperación inexistente, usado o vencido (CA-06)."""


class ClaveRechazada(Exception):
    def __init__(self, problemas: list[str]):
        super().__init__("clave rechazada")
        self.problemas = problemas


class ReautenticacionRequerida(Exception):
    def __init__(self, motivo: str, configurar_mfa: bool = False):
        super().__init__(motivo)
        self.motivo = motivo
        self.configurar_mfa = configurar_mfa


class AccesoDenegado(Exception):
    pass


@dataclass
class ResultadoLogin:
    estado: str  # AUTENTICADO | MFA_REQUERIDO | MFA_CONFIGURAR
    cookie_dispositivo: str | None = None  # valor nuevo a guardar en la cookie, si hace falta
    codigos_recuperacion: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ConfiguracionMfa:
    secreto: str
    uri: str
    qr_svg: str


@dataclass(frozen=True)
class SesionVista:
    id: str
    agente: str
    ip_aproximada: str
    creada: object
    ultima_actividad: object
    actual: bool


# --- Utilidades --------------------------------------------------------------------------------------------------------


def _hash_hmac(*partes: str) -> str:
    return hmac.new(settings.HMAC_TOKENS_CLAVE, ":".join(partes).encode(), hashlib.sha256).hexdigest()


def _ref_cedula(cedula: str) -> str:
    """Referencia no reversible de una cédula para auditoría de intentos sobre cuentas que pueden no existir."""
    return _hash_hmac("cedula", cedula)[:16]


def _normalizar_cedula(texto: str) -> str:
    """Cédula tal como se escribió si tiene forma de cédula (10 dígitos); si no, cadena vacía."""
    t = (texto or "").strip()
    return t if len(t) == 10 and t.isascii() and t.isdigit() else ""


@contextmanager
def _tiempo_minimo():
    """Iguala la duración de los flujos de identidad para que el tiempo no revele si la cédula existe."""
    inicio = time.monotonic()
    try:
        yield
    finally:
        restante = P.tiempo_minimo_respuesta_auth_ms / 1000 - (time.monotonic() - inicio)
        if restante > 0:
            time.sleep(restante)


_hash_ficticio: str | None = None


def _hash_para_igualar() -> str:
    """Hash Argon2id de una clave inexistente: se verifica cuando no hay usuario, para gastar el mismo trabajo."""
    global _hash_ficticio
    if _hash_ficticio is None:
        _hash_ficticio = make_password("clave-ficticia-" + secrets.token_hex(8))
    return _hash_ficticio


def _ip() -> str:
    return contexto.actual().ip or "desconocida"


def _consumir_limite(ambito: str, identificador: str, maximo: int, ventana: int) -> None:
    resultado = limites.consumir(ambito, identificador, maximo, ventana)
    if not resultado.permitido:
        auditoria.registrar(
            accion=Acciones.LIMITE_EXCEDIDO, resultado=auditoria.DENEGADO, detalle={"ambito": ambito}
        )
        raise LimiteExcedido(resultado.reintentar_en)


def _fernet() -> MultiFernet:
    return MultiFernet([Fernet(k.encode()) for k in settings.TOTP_CLAVES_CIFRADO])


def _datos_biograficos(usuario: Usuario) -> dict:
    """Datos para rechazar claves biográficas. La fecha de nacimiento se consulta en el momento y no se guarda."""
    fecha = None
    try:
        datos = integraciones.consultar_afiliacion(usuario.cedula)
        fecha = datos.fecha_nacimiento if datos else None
    except integraciones.ServicioNoDisponible:
        pass  # sin el tercero se aplican igualmente las demás comprobaciones
    return {
        "cedula": usuario.cedula,
        "nombres": usuario.nombres,
        "apellidos": usuario.apellidos,
        "correo": usuario.correo,
        "telefono": usuario.telefono,
        "fecha_nacimiento": fecha,
    }


def _exigir_clave_valida(clave: str, usuario: Usuario) -> None:
    problemas = claves.validar(clave, **_datos_biograficos(usuario))
    if problemas:
        raise ClaveRechazada(problemas)


# --- Bloqueo progresivo por cédula (existente o no: así el bloqueo no revela nada) -----------------------------


def _claves_estado(cedula: str) -> tuple[str, str]:
    base = "login:" + hashlib.sha256(cedula.encode()).hexdigest()[:32]
    return base + ":fallos", base + ":hasta"


def _cache():
    return caches["limites"]


def _bloqueada(cedula: str) -> bool:
    _, k_hasta = _claves_estado(cedula)
    hasta = _cache().get(k_hasta)
    return bool(hasta and reloj.ahora().timestamp() < hasta)


def _registrar_fallo(cedula: str) -> bool:
    """Cuenta un fallo; cada 5 seguidos sube el escalón de espera (1, 5, 15 y 60 min). True si quedó bloqueada."""
    k_fallos, k_hasta = _claves_estado(cedula)
    cache = _cache()
    ttl = P.login_ventana_estado_horas * 3600
    cache.add(k_fallos, 0, timeout=ttl)
    fallos = cache.incr(k_fallos)
    if fallos % P.login_fallos_por_escalon == 0:
        escalon = min(fallos // P.login_fallos_por_escalon - 1, len(P.login_esperas_minutos) - 1)
        cache.set(k_hasta, reloj.ahora().timestamp() + P.login_esperas_minutos[escalon] * 60, timeout=ttl)
        return True
    return False


def _limpiar_fallos(cedula: str) -> None:
    for clave in _claves_estado(cedula):
        _cache().delete(clave)


# --- Activación de cuenta (Anexo F) ----------------------------------------------------------------------------------


def solicitar_activacion(cedula_cruda: str) -> None:
    """Envía un código al contacto REGISTRADO en afiliación. La respuesta es la misma exista o no la cédula (CA-04/05)."""
    cedula = _normalizar_cedula(cedula_cruda)
    _consumir_limite("activacion-ip", _ip(), P.activacion_solicitudes_por_hora_ip, 3600)
    if cedula:
        _consumir_limite("activacion-cedula", cedula, P.activacion_solicitudes_por_hora_cedula, 3600)
    with _tiempo_minimo():
        if not es_valida(cedula):
            auditoria.registrar(
                accion=Acciones.ACTIVACION_SOLICITADA, resultado=auditoria.DENEGADO, detalle={"motivo": "formato"}
            )
            return
        try:
            datos = integraciones.consultar_afiliacion(cedula)
        except integraciones.ServicioNoDisponible:
            auditoria.registrar(
                accion=Acciones.ACTIVACION_SOLICITADA,
                resultado=auditoria.ERROR,
                detalle={"motivo": "afiliacion_no_disponible", "cedula_ref": _ref_cedula(cedula)},
            )
            return
        if datos is None or not datos.vigente or not datos.correo:
            auditoria.registrar(
                accion=Acciones.ACTIVACION_SOLICITADA,
                resultado=auditoria.DENEGADO,
                detalle={"motivo": "sin_afiliacion_vigente", "cedula_ref": _ref_cedula(cedula)},
            )
            return
        with transaction.atomic():
            usuario = Usuario.objects.select_for_update().filter(cedula=cedula).first()
            if usuario is None:
                usuario = Usuario(cedula=cedula, rol=Rol.AFILIADO.value)
                usuario.set_unusable_password()
            if usuario.estado != EstadoCuenta.PENDIENTE_ACTIVACION:
                auditoria.registrar(
                    accion=Acciones.ACTIVACION_SOLICITADA,
                    actor=usuario,
                    resultado=auditoria.DENEGADO,
                    detalle={"motivo": "cuenta_no_pendiente"},
                )
                return
            # La fuente de verdad del contacto es el servicio de afiliación, nunca lo que escriba quien solicita.
            usuario.nombres, usuario.apellidos = datos.nombres, datos.apellidos
            usuario.correo, usuario.telefono = datos.correo, datos.telefono
            try:
                with transaction.atomic():
                    usuario.save()
            except IntegrityError:  # otra solicitud creó la misma cédula a la vez: esa enviará el código
                return
            CodigoActivacion.objects.filter(usuario=usuario, usado=False).update(usado=True)
            codigo = f"{secrets.randbelow(10**P.activacion_digitos):0{P.activacion_digitos}d}"
            ahora = reloj.ahora()
            CodigoActivacion.objects.create(
                usuario=usuario,
                codigo_hash=_hash_hmac("activacion", str(usuario.id), codigo),
                creado=ahora,
                expira=ahora + timedelta(minutes=P.activacion_minutos),
            )
            auditoria.registrar(accion=Acciones.ACTIVACION_SOLICITADA, actor=usuario, recurso_tipo="usuario", recurso_id=usuario.id)
            notificaciones.enviar(
                plantilla="activacion",
                canal="CORREO",
                destino=datos.correo,
                parametros={"codigo": codigo, "minutos": P.activacion_minutos},
                destinatario_id=usuario.id,
            )


def activar_cuenta(cedula_cruda: str, codigo: str, clave_nueva: str) -> None:
    """Valida el código (8 dígitos, 15 min, un solo uso, 5 intentos) y fija la clave elegida por la persona."""
    cedula = _normalizar_cedula(cedula_cruda)
    codigo = (codigo or "").strip()
    _consumir_limite("activacion-ip", _ip(), P.activacion_solicitudes_por_hora_ip * 3, 3600)
    with _tiempo_minimo():
        usuario = Usuario.objects.filter(cedula=cedula, estado=EstadoCuenta.PENDIENTE_ACTIVACION).first() if cedula else None
        if usuario is None:
            _hash_para_igualar()
            auditoria.registrar(
                accion=Acciones.ACTIVACION_FALLIDA, resultado=auditoria.DENEGADO, detalle={"cedula_ref": _ref_cedula(cedula)}
            )
            raise CodigoInvalido()
        resultado = _verificar_codigo_activacion(usuario, codigo)
        if resultado != "OK":
            auditoria.registrar(
                accion=Acciones.ACTIVACION_FALLIDA, actor=usuario, resultado=auditoria.DENEGADO, detalle={"motivo": resultado}
            )
            raise CodigoInvalido()
        # Si la clave se rechaza, el código NO se consume: la persona puede elegir otra.
        _exigir_clave_valida(clave_nueva, usuario)
        with transaction.atomic():
            activo = CodigoActivacion.objects.select_for_update().filter(usuario=usuario, usado=False).first()
            if activo is None:  # otra solicitud lo consumió en paralelo
                raise CodigoInvalido()
            activo.usado = True
            activo.save(update_fields=["usado"])
            usuario.set_password(clave_nueva)
            usuario.estado = EstadoCuenta.ACTIVA.value
            usuario.clave_cambiada_en = reloj.ahora()
            usuario.save(update_fields=["password", "estado", "clave_cambiada_en"])
            auditoria.registrar(accion=Acciones.ACTIVACION_COMPLETADA, actor=usuario, recurso_tipo="usuario", recurso_id=usuario.id)


def _verificar_codigo_activacion(usuario: Usuario, codigo: str) -> str:
    """Comprueba el código y cuenta el intento. Devuelve OK o un motivo. Persiste el conteo aunque falle."""
    with transaction.atomic():
        activo = CodigoActivacion.objects.select_for_update().filter(usuario=usuario, usado=False).first()
        if activo is None:
            return "sin_codigo_vigente"
        if reloj.ahora() >= activo.expira:
            activo.usado = True
            activo.save(update_fields=["usado"])
            return "vencido"
        esperado = _hash_hmac("activacion", str(usuario.id), codigo)
        if hmac.compare_digest(esperado, activo.codigo_hash):
            return "OK"
        activo.intentos += 1
        if activo.intentos >= P.activacion_intentos:
            activo.usado = True  # agotados los intentos: el código se invalida
        activo.save(update_fields=["intentos", "usado"])
        return "agotado" if activo.usado else "incorrecto"


# --- Inicio de sesión ------------------------------------------------------------------------------------------------------


def autenticar(request, cedula_cruda: str, clave: str) -> ResultadoLogin:
    """Verifica cédula y clave. Mismo mensaje y tiempo para cédula inexistente, clave incorrecta o cuenta bloqueada."""
    _consumir_limite("login-ip", _ip(), P.login_intentos_por_minuto_ip, 60)
    cedula = _normalizar_cedula(cedula_cruda)
    with _tiempo_minimo():
        usuario = Usuario.objects.filter(cedula=cedula).first() if es_valida(cedula) else None
        bloqueada = _bloqueada(cedula) if cedula else False
        puede = usuario is not None and politicas.puede_iniciar_sesion(usuario) and usuario.has_usable_password()
        demasiado_larga = len(clave) > P.clave_longitud_maxima
        # Siempre se verifica un hash Argon2id, exista o no la cuenta, para que el tiempo no delate nada.
        coincide = check_password(clave[: P.clave_longitud_maxima], usuario.password if puede else _hash_para_igualar())
        if bloqueada or not puede or not coincide or demasiado_larga:
            nuevo_bloqueo = False if bloqueada or not cedula else _registrar_fallo(cedula)
            auditoria.registrar(
                accion=Acciones.LOGIN_BLOQUEADO if bloqueada else Acciones.LOGIN_FALLIDO,
                actor=usuario if puede else None,
                resultado=auditoria.DENEGADO,
                detalle={"cedula_ref": _ref_cedula(cedula), "bloqueo_nuevo": nuevo_bloqueo},
            )
            raise CredencialesInvalidas()
    _limpiar_fallos(cedula)
    if politicas.requiere_mfa(usuario):
        request.session["preauth"] = {
            "uid": str(usuario.id),
            "hasta": reloj.ahora().timestamp() + P.preautenticacion_minutos * 60,
        }
        estado = "MFA_CONFIGURAR" if politicas.debe_configurar_mfa(usuario) else "MFA_REQUERIDO"
        return ResultadoLogin(estado=estado)
    return _completar_login(request, usuario, con_mfa=False)


def _usuario_preautenticado(request) -> Usuario:
    datos = request.session.get("preauth")
    if not datos or reloj.ahora().timestamp() > datos.get("hasta", 0):
        request.session.pop("preauth", None)
        raise CredencialesInvalidas()
    usuario = Usuario.objects.filter(pk=datos["uid"]).first()
    if usuario is None or not politicas.puede_iniciar_sesion(usuario):
        raise CredencialesInvalidas()
    return usuario


def hay_preautenticacion(request) -> str:
    """Estado del paso intermedio: "", MFA_REQUERIDO o MFA_CONFIGURAR (lo consultan las vistas del segundo factor)."""
    try:
        usuario = _usuario_preautenticado(request)
    except CredencialesInvalidas:
        return ""
    return "MFA_CONFIGURAR" if politicas.debe_configurar_mfa(usuario) else "MFA_REQUERIDO"


def verificar_mfa_login(request, codigo: str) -> ResultadoLogin:
    """Segundo paso del inicio de sesión: TOTP o código de recuperación. Los fallos alimentan el bloqueo de la cuenta."""
    _consumir_limite("mfa-ip", _ip(), P.totp_intentos_por_minuto_ip, 60)
    usuario = _usuario_preautenticado(request)
    if _bloqueada(usuario.cedula):
        raise CredencialesInvalidas()
    if not _aceptar_codigo_mfa(usuario, codigo):
        _registrar_fallo(usuario.cedula)
        auditoria.registrar(accion=Acciones.MFA_FALLIDO, actor=usuario, resultado=auditoria.DENEGADO)
        raise CodigoInvalido()
    _limpiar_fallos(usuario.cedula)
    request.session.pop("preauth", None)
    auditoria.registrar(accion=Acciones.MFA_OK, actor=usuario)
    return _completar_login(request, usuario, con_mfa=True)


def _aceptar_codigo_mfa(usuario: Usuario, codigo: str) -> bool:
    codigo = (codigo or "").strip()
    if not codigo:
        return False
    if codigo.isdigit() and len(codigo) == P.totp_digitos:
        return _verificar_totp(usuario, codigo)
    return _usar_codigo_recuperacion(usuario, codigo)


def _verificar_totp(usuario: Usuario, codigo: str) -> bool:
    if not usuario.totp_confirmado or not usuario.totp_secreto_cifrado:
        return False
    try:
        secreto = _fernet().decrypt(usuario.totp_secreto_cifrado.encode()).decode()
    except InvalidToken:
        return False
    return _comprobar_totp_y_registrar(usuario, secreto, codigo)


def _comprobar_totp_y_registrar(usuario: Usuario, secreto: str, codigo: str) -> bool:
    """Acepta el código del paso actual ± tolerancia. Un código ya usado (o anterior) se rechaza: sin repetición."""
    totp = pyotp.TOTP(secreto, digits=P.totp_digitos, interval=P.totp_periodo_segundos)
    ahora = int(reloj.ahora().timestamp())
    for delta in range(-P.totp_tolerancia_pasos, P.totp_tolerancia_pasos + 1):
        instante = ahora + delta * P.totp_periodo_segundos
        if hmac.compare_digest(totp.at(instante), codigo):
            paso = instante // P.totp_periodo_segundos
            # Actualización condicional: dos solicitudes con el mismo código no pueden aceptarlo ambas.
            return Usuario.objects.filter(pk=usuario.pk, totp_ultimo_paso__lt=paso).update(totp_ultimo_paso=paso) == 1
    return False


def _normalizar_codigo_recuperacion(codigo: str) -> str:
    return "".join(ch for ch in codigo.lower() if ch.isalnum())


def _usar_codigo_recuperacion(usuario: Usuario, codigo: str) -> bool:
    resumen = hashlib.sha256(_normalizar_codigo_recuperacion(codigo).encode()).hexdigest()
    usado = CodigoRecuperacionMfa.objects.filter(usuario=usuario, codigo_hash=resumen, usado=False).update(
        usado=True, usado_en=reloj.ahora()
    )
    if usado == 1:
        auditoria.registrar(accion=Acciones.MFA_CODIGO_RECUPERACION_USADO, actor=usuario)
    return usado == 1


def _generar_codigos_recuperacion(usuario: Usuario) -> list[str]:
    """Reemplaza los códigos previos por 10 nuevos de un solo uso. Solo se guardan sus resúmenes."""
    CodigoRecuperacionMfa.objects.filter(usuario=usuario).delete()
    visibles = []
    for _ in range(P.totp_codigos_recuperacion):
        crudo = secrets.token_hex(10)  # 80 bits
        visibles.append("-".join(crudo[i : i + 5] for i in range(0, 20, 5)))
        CodigoRecuperacionMfa.objects.create(usuario=usuario, codigo_hash=hashlib.sha256(crudo.encode()).hexdigest())
    return visibles


# --- Configuración del segundo factor ------------------------------------------------------------------------------------------


def _usuario_para_mfa(request) -> tuple[Usuario, bool]:
    """Usuario que configura el segundo factor: el autenticado, o el preautenticado (funcionario en su primer ingreso)."""
    if request.user.is_authenticated:
        return request.user, False
    return _usuario_preautenticado(request), True


def obtener_configuracion_mfa(request) -> ConfiguracionMfa:
    """Datos para enrolar el autenticador. Reutiliza el secreto pendiente si ya se generó (un error de tipeo no
    debe obligar a escanear otro código QR)."""
    usuario, en_preauth = _usuario_para_mfa(request)
    if usuario.totp_confirmado:
        if en_preauth:
            raise AccesoDenegado()  # quien solo tiene la clave no puede reemplazar el segundo factor
        exigir_operacion_sensible(request)  # cambiar un factor ya configurado es una operación sensible
    pendiente = request.session.get("mfa_pendiente")
    secreto = None
    if pendiente:
        try:
            secreto = _fernet().decrypt(pendiente.encode()).decode()
        except InvalidToken:
            secreto = None
    if secreto is None:
        secreto = pyotp.random_base32()
        request.session["mfa_pendiente"] = _fernet().encrypt(secreto.encode()).decode()
    uri = pyotp.TOTP(secreto, digits=P.totp_digitos, interval=P.totp_periodo_segundos).provisioning_uri(
        name=usuario.cedula, issuer_name="Portal de turnos IESS (prueba)"
    )
    qr = segno.make(uri, error="m").svg_inline(scale=5, border=2)
    return ConfiguracionMfa(secreto=secreto, uri=uri, qr_svg=mark_safe(qr))  # SVG generado por segno a partir del URI propio


def confirmar_configuracion_mfa(request, codigo: str) -> ResultadoLogin:
    """Confirma el primer código TOTP, guarda el secreto cifrado y entrega los 10 códigos de recuperación (una vez)."""
    _consumir_limite("mfa-ip", _ip(), P.totp_intentos_por_minuto_ip, 60)
    usuario, en_preauth = _usuario_para_mfa(request)
    pendiente = request.session.get("mfa_pendiente")
    if not pendiente:
        raise CodigoInvalido()
    secreto = _fernet().decrypt(pendiente.encode()).decode()
    if not _comprobar_totp_y_registrar(usuario, secreto, (codigo or "").strip()):
        auditoria.registrar(accion=Acciones.MFA_FALLIDO, actor=usuario, resultado=auditoria.DENEGADO, detalle={"fase": "configuracion"})
        raise CodigoInvalido()
    with transaction.atomic():
        usuario.totp_secreto_cifrado = _fernet().encrypt(secreto.encode()).decode()
        usuario.totp_confirmado = True
        usuario.save(update_fields=["totp_secreto_cifrado", "totp_confirmado"])
        codigos = _generar_codigos_recuperacion(usuario)
        auditoria.registrar(accion=Acciones.MFA_CONFIGURADO, actor=usuario)
    request.session.pop("mfa_pendiente", None)
    if en_preauth:
        request.session.pop("preauth", None)
        resultado = _completar_login(request, usuario, con_mfa=True)
    else:
        request.session["mfa_en"] = reloj.ahora().timestamp()
        resultado = ResultadoLogin(estado="AUTENTICADO")
    resultado.codigos_recuperacion = codigos
    return resultado


# --- Sesiones, dispositivos y actividad ----------------------------------------------------------------------------------


def _agente_resumido(user_agent: str) -> str:
    ua = (user_agent or "").lower()
    navegador = next((n for n, k in (("Edge", "edg/"), ("Opera", "opr/"), ("Chrome", "chrome/"), ("Firefox", "firefox/"), ("Safari", "safari/")) if k in ua), "Navegador")
    sistema = next((n for n, k in (("Windows", "windows"), ("Android", "android"), ("iPhone/iPad", "iphone"), ("iPhone/iPad", "ipad"), ("macOS", "mac os"), ("Linux", "linux")) if k in ua), "dispositivo desconocido")
    return f"{navegador} en {sistema}"


def _ip_aproximada(ip: str | None) -> str:
    """Nunca se muestra ni se guarda la IP completa de la sesión (§4.5): solo el prefijo."""
    if not ip:
        return ""
    if ":" in ip:
        return ":".join(ip.split(":")[:3]) + "::/48"
    partes = ip.split(".")
    return ".".join(partes[:2]) + ".x.x" if len(partes) == 4 else ""


def _revocar_fila(sesion: SesionDispositivo, motivo: str) -> None:
    Session.objects.filter(session_key=sesion.session_key).delete()
    sesion.revocada_en = reloj.ahora()
    sesion.motivo_revocacion = motivo
    sesion.save(update_fields=["revocada_en", "motivo_revocacion"])


def _revocar_todas(usuario: Usuario, motivo: str, excepto_key: str | None = None) -> int:
    n = 0
    for sesion in SesionDispositivo.objects.filter(usuario=usuario, revocada_en__isnull=True):
        if sesion.session_key != excepto_key:
            _revocar_fila(sesion, motivo)
            n += 1
    return n


def _purgar_sesiones_muertas(usuario: Usuario) -> None:
    activas = list(SesionDispositivo.objects.filter(usuario=usuario, revocada_en__isnull=True))
    vivas = set(
        Session.objects.filter(session_key__in=[s.session_key for s in activas], expire_date__gt=reloj.ahora()).values_list(
            "session_key", flat=True
        )
    )
    for sesion in activas:
        if sesion.session_key not in vivas:
            sesion.revocada_en = reloj.ahora()
            sesion.motivo_revocacion = "EXPIRADA"
            sesion.save(update_fields=["revocada_en", "motivo_revocacion"])


def _completar_login(request, usuario: Usuario, *, con_mfa: bool) -> ResultadoLogin:
    """Crea la sesión (24 h absolutas), registra el dispositivo y aplica el máximo de 3 dispositivos."""
    ahora = reloj.ahora()
    cookie_actual = request.COOKIES.get(settings.COOKIE_DISPOSITIVO, "")
    cookie_nueva = None
    if not (16 <= len(cookie_actual) <= 64 and cookie_actual.replace("-", "").replace("_", "").isalnum()):
        cookie_actual = secrets.token_urlsafe(24)
        cookie_nueva = cookie_actual
    dispositivo_hash = hashlib.sha256(cookie_actual.encode()).hexdigest()

    with transaction.atomic():
        Usuario.objects.select_for_update().filter(pk=usuario.pk).first()  # serializa inicios de sesión simultáneos
        _purgar_sesiones_muertas(usuario)
        primer_ingreso = not SesionDispositivo.objects.filter(usuario=usuario).exists()
        conocido = SesionDispositivo.objects.filter(usuario=usuario, dispositivo_hash=dispositivo_hash).exists()
        # El mismo dispositivo reemplaza su sesión anterior en lugar de contar como otro.
        for previa in SesionDispositivo.objects.filter(usuario=usuario, dispositivo_hash=dispositivo_hash, revocada_en__isnull=True):
            _revocar_fila(previa, "REEMPLAZADA")
        activas = list(SesionDispositivo.objects.filter(usuario=usuario, revocada_en__isnull=True).order_by("ultima_actividad"))
        expulsadas = 0
        while len(activas) >= P.sesion_dispositivos_maximos:
            _revocar_fila(activas.pop(0), "LIMITE_DISPOSITIVOS")
            expulsadas += 1

        # Sesión nueva con clave rotada y vencimiento ABSOLUTO (no se renueva con la actividad).
        request.session.pop("preauth", None)
        django_login(request, usuario, backend="django.contrib.auth.backends.ModelBackend")
        request.session.set_expiry(timedelta(hours=P.sesion_horas_totales))
        request.session["iniciada_en"] = ahora.timestamp()
        request.session["ultima_actividad"] = ahora.timestamp()
        if con_mfa:
            request.session["mfa_en"] = ahora.timestamp()
        request.session.save()
        SesionDispositivo.objects.create(
            usuario=usuario,
            session_key=request.session.session_key,
            dispositivo_hash=dispositivo_hash,
            agente=_agente_resumido(request.META.get("HTTP_USER_AGENT", "")),
            ip_aproximada=_ip_aproximada(contexto.actual().ip),
            creada=ahora,
            ultima_actividad=ahora,
        )
        auditoria.registrar(accion=Acciones.LOGIN_OK, actor=usuario, detalle={"mfa": con_mfa})
        auditoria.registrar(accion=Acciones.SESION_INICIADA, actor=usuario, recurso_tipo="sesion")
        if expulsadas:
            auditoria.registrar(accion=Acciones.SESION_REVOCADA, actor=usuario, detalle={"motivo": "LIMITE_DISPOSITIVOS", "cantidad": expulsadas})
        if not conocido and not primer_ingreso:
            auditoria.registrar(accion=Acciones.DISPOSITIVO_NUEVO, actor=usuario)
    if expulsadas:
        notificaciones.enviar(plantilla="sesion_revocada", canal="CORREO", destino=usuario.correo, destinatario_id=usuario.id)
    if not conocido and not primer_ingreso:
        # Aviso neutral: fecha y hora, sin IP completa ni ubicación exacta.
        notificaciones.enviar(
            plantilla="dispositivo_nuevo",
            canal="CORREO",
            destino=usuario.correo,
            parametros={"fecha": zona.localtime(ahora).strftime("%d/%m/%Y %H:%M")},
            destinatario_id=usuario.id,
        )
    return ResultadoLogin(estado="AUTENTICADO", cookie_dispositivo=cookie_nueva)


def registrar_actividad(request) -> None:
    """Lo llama el middleware en cada solicitud autenticada: vence la sesión a las 24 h y anota la actividad."""
    sesion = request.session
    ahora = reloj.ahora().timestamp()
    iniciada = sesion.get("iniciada_en")
    if iniciada is not None and ahora - iniciada > P.sesion_horas_totales * 3600:
        _cerrar_sesion_actual(request, "EXPIRADA")
        return
    ultima = sesion.get("ultima_actividad")
    request.actividad_previa = ultima  # lo que importa para una operación sensible es la actividad ANTERIOR a esta
    if ultima is None or ahora - ultima >= P.sesion_actualizar_actividad_segundos:
        sesion["ultima_actividad"] = ahora
        SesionDispositivo.objects.filter(session_key=sesion.session_key).update(ultima_actividad=reloj.ahora())


def _cerrar_sesion_actual(request, motivo: str) -> None:
    clave = request.session.session_key
    usuario = request.user if request.user.is_authenticated else None
    SesionDispositivo.objects.filter(session_key=clave, revocada_en__isnull=True).update(
        revocada_en=reloj.ahora(), motivo_revocacion=motivo
    )
    django_logout(request)
    if usuario is not None:
        auditoria.registrar(accion=Acciones.SESION_CERRADA, actor=usuario, detalle={"motivo": motivo})


def cerrar_sesion(request) -> None:
    _cerrar_sesion_actual(request, "CIERRE_VOLUNTARIO")


def listar_sesiones(request) -> list[SesionVista]:
    """Sesiones activas de la persona autenticada (solo las suyas)."""
    usuario = request.user
    _purgar_sesiones_muertas(usuario)
    actual = request.session.session_key
    filas = SesionDispositivo.objects.filter(usuario=usuario, revocada_en__isnull=True).order_by("-ultima_actividad")
    return [
        SesionVista(str(f.id), f.agente, f.ip_aproximada, f.creada, f.ultima_actividad, f.session_key == actual) for f in filas
    ]


def revocar_sesion(request, sesion_id) -> bool:
    """Revoca una sesión propia. Devuelve False si no existe o no es suya (misma respuesta en ambos casos)."""
    try:
        fila = SesionDispositivo.objects.filter(pk=sesion_id, revocada_en__isnull=True).first()
    except (ValueError, TypeError, ValidationError):
        return False
    if fila is None or not politicas.puede_gestionar_sesion(request.user, fila.usuario_id):
        if fila is not None:
            auditoria.registrar(accion=Acciones.ACCESO_DENEGADO, actor=request.user, resultado=auditoria.DENEGADO, recurso_tipo="sesion", recurso_id=sesion_id)
        return False
    es_actual = fila.session_key == request.session.session_key
    with transaction.atomic():
        _revocar_fila(fila, "REVOCADA_POR_EL_USUARIO")
        auditoria.registrar(accion=Acciones.SESION_REVOCADA, actor=request.user, recurso_tipo="sesion", recurso_id=fila.id)
    if es_actual:
            django_logout(request)
    return True


# --- Operaciones sensibles y cambio de clave -------------------------------------------------------------------------------


def exigir_operacion_sensible(request) -> None:
    """Para operaciones sensibles: sesión con actividad reciente (1 h) y segundo factor reciente."""
    usuario = request.user
    if not usuario.totp_confirmado:
        raise ReautenticacionRequerida("MFA_NO_CONFIGURADO", configurar_mfa=True)
    motivo = politicas.sesion_apta_para_operacion_sensible(
        ahora_ts=reloj.ahora().timestamp(),
        ultima_actividad_ts=getattr(request, "actividad_previa", request.session.get("ultima_actividad")),
        mfa_en_ts=request.session.get("mfa_en"),
    )
    if motivo:
        raise ReautenticacionRequerida(motivo)


def reautenticar(request, clave: str, codigo: str) -> None:
    """Paso de refuerzo: clave actual + segundo factor, para retomar una operación sensible."""
    _consumir_limite("reauth-ip", _ip(), P.login_intentos_por_minuto_ip, 60)
    usuario = request.user
    if _bloqueada(usuario.cedula):
        raise CredencialesInvalidas()
    with _tiempo_minimo():
        ok_clave = check_password(clave[: P.clave_longitud_maxima], usuario.password) and len(clave) <= P.clave_longitud_maxima
        ok_mfa = _aceptar_codigo_mfa(usuario, codigo)
        if not (ok_clave and ok_mfa):
            _registrar_fallo(usuario.cedula)
            auditoria.registrar(accion=Acciones.MFA_FALLIDO, actor=usuario, resultado=auditoria.DENEGADO, detalle={"fase": "reautenticacion"})
            raise CredencialesInvalidas()
    _limpiar_fallos(usuario.cedula)
    ahora = reloj.ahora().timestamp()
    request.session["mfa_en"] = ahora
    request.session["ultima_actividad"] = ahora
    request.actividad_previa = ahora


def cambiar_clave(request, clave_actual: str, clave_nueva: str) -> None:
    """Cambia la clave (operación sensible) y cierra TODAS las sesiones, incluida la actual."""
    exigir_operacion_sensible(request)
    usuario = request.user
    if not check_password(clave_actual[: P.clave_longitud_maxima], usuario.password) or len(clave_actual) > P.clave_longitud_maxima:
        auditoria.registrar(accion=Acciones.CLAVE_CAMBIADA, actor=usuario, resultado=auditoria.DENEGADO, detalle={"motivo": "clave_actual_incorrecta"})
        raise CredencialesInvalidas()
    _exigir_clave_valida(clave_nueva, usuario)
    if check_password(clave_nueva, usuario.password):
        raise ClaveRechazada(["La nueva clave debe ser distinta de la actual."])
    with transaction.atomic():
        usuario.set_password(clave_nueva)
        usuario.clave_cambiada_en = reloj.ahora()
        usuario.save(update_fields=["password", "clave_cambiada_en"])
        _revocar_todas(usuario, "CLAVE_CAMBIADA")
        auditoria.registrar(accion=Acciones.CLAVE_CAMBIADA, actor=usuario, recurso_tipo="usuario", recurso_id=usuario.id)
    notificaciones.enviar(plantilla="clave_cambiada", canal="CORREO", destino=usuario.correo, destinatario_id=usuario.id)
    django_logout(request)  # la sesión actual ya fue revocada en la base; se limpia también la del request


# --- Recuperación de clave (Anexo F) ------------------------------------------------------------------------------------------------


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def solicitar_recuperacion(cedula_cruda: str) -> None:
    """Envía el enlace al contacto REGISTRADO. Mensaje y tiempo idénticos exista o no la cédula (CWE-204)."""
    cedula = _normalizar_cedula(cedula_cruda)
    _consumir_limite("recuperacion-ip", _ip(), P.recuperacion_solicitudes_por_hora_ip, 3600)
    if cedula:
        _consumir_limite("recuperacion-cedula", cedula, P.recuperacion_solicitudes_por_hora_cedula, 3600)
    with _tiempo_minimo():
        usuario = Usuario.objects.filter(cedula=cedula, estado=EstadoCuenta.ACTIVA).first() if es_valida(cedula) else None
        if usuario is None or not usuario.correo:
            secrets.token_urlsafe(P.recuperacion_bits_token // 8)  # mismo trabajo criptográfico en ambos caminos
            auditoria.registrar(
                accion=Acciones.RECUPERACION_SOLICITADA, resultado=auditoria.DENEGADO, detalle={"cedula_ref": _ref_cedula(cedula)}
            )
            return
        token = secrets.token_urlsafe(P.recuperacion_bits_token // 8)  # 128 bits
        with transaction.atomic():
            TokenRecuperacion.objects.filter(usuario=usuario, usado=False).update(usado=True, usado_en=reloj.ahora())
            ahora = reloj.ahora()
            TokenRecuperacion.objects.create(
                usuario=usuario,
                token_hash=_hash_token(token),
                creado=ahora,
                expira=ahora + timedelta(minutes=P.recuperacion_minutos),
            )
            auditoria.registrar(accion=Acciones.RECUPERACION_SOLICITADA, actor=usuario, recurso_tipo="usuario", recurso_id=usuario.id)
        # El enlace usa el dominio FIJO de la configuración, jamás la cabecera Host (CA-16).
        enlace = f"{settings.URL_BASE}/cuenta/restablecer/{token}/"
        notificaciones.enviar(
            plantilla="recuperacion",
            canal="CORREO",
            destino=usuario.correo,
            parametros={"enlace": enlace, "minutos": P.recuperacion_minutos},
            destinatario_id=usuario.id,
        )


def token_recuperacion_vigente(token: str) -> bool:
    """Para mostrar el formulario: ¿el token existe, no se usó y no venció? (no lo consume)."""
    fila = TokenRecuperacion.objects.filter(token_hash=_hash_token(token or ""), usado=False).first()
    return bool(fila and reloj.ahora() < fila.expira)


def restablecer_clave(token: str, clave_nueva: str, codigo_mfa: str = "") -> None:
    """Consume el token en UNA transacción: dos solicitudes con el mismo token no pueden usarlo ambas (CA-06)."""
    _consumir_limite("recuperacion-ip", _ip(), P.recuperacion_solicitudes_por_hora_ip * 3, 3600)
    usuario = None
    rechazo: Exception | None = None
    with _tiempo_minimo():
        try:
            with transaction.atomic():
                fila = (
                    TokenRecuperacion.objects.select_for_update()
                    .select_related("usuario")
                    .filter(token_hash=_hash_token(token or ""), usado=False)
                    .first()
                )
                if fila is None or reloj.ahora() >= fila.expira or not hmac.compare_digest(fila.token_hash, _hash_token(token)):
                    raise TokenInvalido()
                usuario = fila.usuario
                if usuario.estado != EstadoCuenta.ACTIVA:
                    raise TokenInvalido()
                # Quien tenga segundo factor lo conserva: leer el correo no basta para tomar la cuenta.
                if usuario.totp_confirmado and not _aceptar_codigo_mfa(usuario, codigo_mfa):
                    raise CodigoInvalido()
                _exigir_clave_valida(clave_nueva, usuario)  # si la clave se rechaza, el token sigue vigente
                fila.usado = True
                fila.usado_en = reloj.ahora()
                fila.save(update_fields=["usado", "usado_en"])
                usuario.set_password(clave_nueva)
                usuario.clave_cambiada_en = reloj.ahora()
                usuario.save(update_fields=["password", "clave_cambiada_en"])
                _revocar_todas(usuario, "RECUPERACION_DE_CLAVE")  # al terminar se cierran todas las sesiones
                auditoria.registrar(
                    accion=Acciones.RECUPERACION_COMPLETADA, actor=usuario, recurso_tipo="usuario", recurso_id=usuario.id
                )
        except (TokenInvalido, CodigoInvalido, ClaveRechazada) as error:
            rechazo = error
    if rechazo is not None:
        # La auditoría de un rechazo se escribe FUERA de la transacción revertida, para que quede registrada.
        if isinstance(rechazo, CodigoInvalido) and usuario is not None:
            _registrar_fallo(usuario.cedula)
        auditoria.registrar(
            accion=Acciones.RECUPERACION_FALLIDA,
            actor=usuario,
            resultado=auditoria.DENEGADO,
            detalle={"motivo": type(rechazo).__name__},
        )
        raise rechazo
    notificaciones.enviar(plantilla="clave_cambiada", canal="CORREO", destino=usuario.correo, destinatario_id=usuario.id)


# --- Aprovisionamiento administrativo (siembra de datos de demostración) -----------------------------------------------------------------


def precargar_afiliado(*, cedula: str, nombres: str, apellidos: str, correo: str, telefono: str, rol: str = Rol.AFILIADO.value) -> Usuario:
    """Crea una cuenta PENDIENTE de activación con los datos del servicio de afiliación. Sin clave: solo se activa por código."""
    usuario, creado = Usuario.objects.get_or_create(
        cedula=cedula,
        defaults={"nombres": nombres, "apellidos": apellidos, "correo": correo, "telefono": telefono, "rol": rol,
                  "password": make_password(None)},
    )
    if creado:
        auditoria.registrar(accion=Acciones.SIEMBRA, recurso_tipo="usuario", recurso_id=usuario.id, detalle={"rol": rol})
    return usuario


def aprovisionar_cuenta(*, cedula: str, rol: str, nombres: str, apellidos: str, correo: str, telefono: str, clave: str, establecimiento_id=None) -> tuple[Usuario, bool]:
    """Alta administrativa de una cuenta ACTIVA (funcionarios y cuentas de demostración). Aplica las reglas de clave.

    Los funcionarios deberán configurar su segundo factor en su primer ingreso (no pueden operar sin él).
    """
    existente = Usuario.objects.filter(cedula=cedula).first()
    if existente is not None:
        return existente, False  # idempotente: no se toca la clave de una cuenta que ya existe
    problemas = claves.validar(clave, cedula=cedula, nombres=nombres, apellidos=apellidos, correo=correo, telefono=telefono)
    if problemas:
        raise ClaveRechazada(problemas)
    with transaction.atomic():
        usuario = Usuario(cedula=cedula, rol=rol, nombres=nombres, apellidos=apellidos, correo=correo, telefono=telefono,
                          estado=EstadoCuenta.ACTIVA.value, establecimiento_id=establecimiento_id, clave_cambiada_en=reloj.ahora())
        usuario.set_password(clave)
        usuario.save()
        auditoria.registrar(accion=Acciones.SIEMBRA, recurso_tipo="usuario", recurso_id=usuario.id, detalle={"rol": rol, "alta": "aprovisionada"})
    return usuario, True

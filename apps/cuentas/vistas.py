"""Vistas de cuenta: finas. Autentican, validan el esquema, llaman a UN servicio y responden.

Sin reglas de negocio, sin ORM y sin comprobar permisos con `if` propios (eso vive en servicios y políticas).
Todos los mensajes de error son genéricos: no revelan si una cédula existe (CA-05).
"""
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme, urlencode
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from . import esquemas, servicios

MENSAJE_LOGIN = "No fue posible iniciar sesión. Verifique su cédula y su clave, o intente más tarde."
MENSAJE_LIMITE = "Demasiados intentos. Espere unos minutos antes de volver a intentarlo."
MENSAJE_CODIGO = "El código no es válido o ya venció. Solicite uno nuevo si hace falta."


def _limitado(request, error: servicios.LimiteExcedido):
    respuesta = render(request, "errores/429.html", status=429)
    respuesta["Retry-After"] = str(max(1, error.reintentar_en))
    return respuesta


def _destino_seguro(request, por_defecto: str = "inicio") -> str:
    """Valida el parámetro `next` (CWE-601): solo rutas del propio sitio."""
    candidato = request.POST.get("next") or request.GET.get("next") or ""
    if candidato and url_has_allowed_host_and_scheme(candidato, allowed_hosts={request.get_host()}, require_https=True):
        return candidato
    return reverse(por_defecto)


def _responder_login(request, resultado: servicios.ResultadoLogin, destino: str):
    """Termina un inicio de sesión: fija la cookie de dispositivo y, si corresponde, muestra los códigos de recuperación."""
    if resultado.codigos_recuperacion:
        respuesta = render(request, "cuentas/codigos_recuperacion.html", {"codigos": resultado.codigos_recuperacion, "destino": destino})
    else:
        respuesta = redirect(destino)
    if resultado.cookie_dispositivo:
        respuesta.set_cookie(
            settings.COOKIE_DISPOSITIVO,
            resultado.cookie_dispositivo,
            max_age=365 * 24 * 3600,
            secure=True,
            httponly=True,
            samesite="Lax",
            path="/",
        )
    return respuesta


# --- Activación ------------------------------------------------------------------------------------------------------------


@require_http_methods(["GET", "POST"])
def solicitar_activacion(request):
    form = esquemas.SolicitudActivacionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            servicios.solicitar_activacion(form.cleaned_data["cedula"])
        except servicios.LimiteExcedido as error:
            return _limitado(request, error)
        # Respuesta idéntica exista o no la cédula.
        return render(request, "cuentas/activacion_enviada.html", {"cedula": form.cleaned_data["cedula"]})
    return render(request, "cuentas/solicitar_activacion.html", {"form": form})


@require_http_methods(["GET", "POST"])
def confirmar_activacion(request):
    inicial = {"cedula": request.GET.get("cedula", "")[:10]}
    form = esquemas.ConfirmarActivacionForm(request.POST or None, initial=inicial)
    if request.method == "POST" and form.is_valid():
        datos = form.cleaned_data
        try:
            servicios.activar_cuenta(datos["cedula"], datos["codigo"], datos["clave_nueva"])
        except servicios.LimiteExcedido as error:
            return _limitado(request, error)
        except servicios.CodigoInvalido:
            form.add_error(None, MENSAJE_CODIGO)
        except servicios.ClaveRechazada as error:
            for problema in error.problemas:
                form.add_error("clave_nueva", problema)
        else:
            messages.success(request, "Su cuenta fue activada. Ya puede iniciar sesión.")
            return redirect("cuentas:ingresar")
    return render(request, "cuentas/confirmar_activacion.html", {"form": form})


# --- Ingreso y segundo factor --------------------------------------------------------------------------------------------


@require_http_methods(["GET", "POST"])
@never_cache
def ingresar(request):
    form = esquemas.IngresoForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            resultado = servicios.autenticar(request, form.cleaned_data["cedula"], form.cleaned_data["clave"])
        except servicios.LimiteExcedido as error:
            return _limitado(request, error)
        except servicios.CredencialesInvalidas:
            form.add_error(None, MENSAJE_LOGIN)
        else:
            destino = _destino_seguro(request)
            siguiente = urlencode({"next": destino})
            if resultado.estado == "MFA_REQUERIDO":
                return redirect(f"{reverse('cuentas:mfa')}?{siguiente}")
            if resultado.estado == "MFA_CONFIGURAR":
                return redirect(f"{reverse('cuentas:mfa_configurar')}?{siguiente}")
            return _responder_login(request, resultado, destino)
    return render(request, "cuentas/ingresar.html", {"form": form, "next": request.GET.get("next", "")})


@require_http_methods(["GET", "POST"])
@never_cache
def verificar_mfa(request):
    if not servicios.hay_preautenticacion(request):
        return redirect("cuentas:ingresar")
    form = esquemas.CodigoMfaForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            resultado = servicios.verificar_mfa_login(request, form.cleaned_data["codigo"])
        except servicios.LimiteExcedido as error:
            return _limitado(request, error)
        except servicios.CredencialesInvalidas:
            return redirect("cuentas:ingresar")
        except servicios.CodigoInvalido:
            form.add_error(None, "El código no es válido. Verifique e intente de nuevo.")
        else:
            return _responder_login(request, resultado, _destino_seguro(request))
    return render(request, "cuentas/mfa.html", {"form": form, "next": request.GET.get("next", "")})


@require_http_methods(["GET", "POST"])
@never_cache
def configurar_mfa(request):
    """Funcionarios en su primer ingreso (preautenticados) y personas ya autenticadas que activan el segundo factor."""
    try:
        if not request.user.is_authenticated and not servicios.hay_preautenticacion(request):
            return redirect("cuentas:ingresar")
        form = esquemas.CodigoMfaForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            try:
                resultado = servicios.confirmar_configuracion_mfa(request, form.cleaned_data["codigo"])
            except servicios.CodigoInvalido:
                form.add_error(None, "El código no es válido. Verifique que la hora de su dispositivo sea correcta.")
            else:
                return _responder_login(request, resultado, _destino_seguro(request))
        configuracion = servicios.obtener_configuracion_mfa(request)
    except servicios.LimiteExcedido as error:
        return _limitado(request, error)
    except servicios.ReautenticacionRequerida:
        return redirect(f"{reverse('cuentas:reautenticar')}?{urlencode({'next': reverse('cuentas:mfa_configurar')})}")
    except (servicios.AccesoDenegado, servicios.CredencialesInvalidas):
        return redirect("cuentas:ingresar")
    return render(request, "cuentas/mfa_configurar.html", {"form": form, "configuracion": configuracion, "next": request.GET.get("next", "")})


@login_required
@require_http_methods(["GET", "POST"])
@never_cache
def reautenticar(request):
    form = esquemas.ReautenticarForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            servicios.reautenticar(request, form.cleaned_data["clave"], form.cleaned_data["codigo"])
        except servicios.LimiteExcedido as error:
            return _limitado(request, error)
        except servicios.CredencialesInvalidas:
            form.add_error(None, "No pudimos verificar sus datos. Revise la clave y el código e intente de nuevo.")
        else:
            return redirect(_destino_seguro(request))
    return render(request, "cuentas/reautenticar.html", {"form": form, "next": request.GET.get("next", "")})


@require_POST
def salir(request):
    servicios.cerrar_sesion(request)
    messages.info(request, "Sesión cerrada.")
    return redirect("cuentas:ingresar")


# --- Sesiones y clave -------------------------------------------------------------------------------------------------------


@login_required
@require_http_methods(["GET"])
def sesiones(request):
    return render(request, "cuentas/sesiones.html", {"sesiones": servicios.listar_sesiones(request)})


@login_required
@require_POST
def revocar_sesion(request, sesion_id):
    if not servicios.revocar_sesion(request, sesion_id):
        raise Http404()  # misma respuesta para "no existe" y "no es suya"
    messages.success(request, "La sesión fue cerrada.")
    return redirect("cuentas:sesiones" if request.user.is_authenticated else "cuentas:ingresar")


@login_required
@require_http_methods(["GET", "POST"])
@never_cache
def cambiar_clave(request):
    form = esquemas.CambiarClaveForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        datos = form.cleaned_data
        try:
            servicios.cambiar_clave(request, datos["clave_actual"], datos["clave_nueva"])
        except servicios.ReautenticacionRequerida as error:
            if error.configurar_mfa:
                messages.info(request, "Para cambiar la clave primero active un segundo factor de autenticación.")
                return redirect(f"{reverse('cuentas:mfa_configurar')}?{urlencode({'next': reverse('cuentas:cambiar_clave')})}")
            return redirect(f"{reverse('cuentas:reautenticar')}?{urlencode({'next': reverse('cuentas:cambiar_clave')})}")
        except servicios.CredencialesInvalidas:
            form.add_error("clave_actual", "La clave actual no es correcta.")
        except servicios.ClaveRechazada as error:
            for problema in error.problemas:
                form.add_error("clave_nueva", problema)
        else:
            messages.success(request, "Su clave fue cambiada y se cerraron todas sus sesiones. Inicie sesión de nuevo.")
            return redirect("cuentas:ingresar")
    return render(request, "cuentas/cambiar_clave.html", {"form": form})


# --- Recuperación ------------------------------------------------------------------------------------------------------------


@require_http_methods(["GET", "POST"])
def solicitar_recuperacion(request):
    form = esquemas.SolicitudRecuperacionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            servicios.solicitar_recuperacion(form.cleaned_data["cedula"])
        except servicios.LimiteExcedido as error:
            return _limitado(request, error)
        return render(request, "cuentas/recuperacion_enviada.html")  # mismo mensaje exista o no la cédula
    return render(request, "cuentas/solicitar_recuperacion.html", {"form": form})


@require_http_methods(["GET", "POST"])
@never_cache
def restablecer_clave(request, token):
    if len(token) > 64 or not servicios.token_recuperacion_vigente(token):
        return render(request, "cuentas/token_invalido.html", status=410)
    form = esquemas.RestablecerClaveForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        datos = form.cleaned_data
        try:
            servicios.restablecer_clave(token, datos["clave_nueva"], datos["codigo_mfa"])
        except servicios.LimiteExcedido as error:
            return _limitado(request, error)
        except servicios.TokenInvalido:
            return render(request, "cuentas/token_invalido.html", status=410)
        except servicios.CodigoInvalido:
            form.add_error("codigo_mfa", "El código del segundo factor no es válido.")
        except servicios.ClaveRechazada as error:
            for problema in error.problemas:
                form.add_error("clave_nueva", problema)
        else:
            messages.success(request, "Su clave fue restablecida. Inicie sesión con la nueva clave.")
            return redirect("cuentas:ingresar")
    respuesta = render(request, "cuentas/restablecer_clave.html", {"form": form})
    respuesta["Referrer-Policy"] = "no-referrer"  # el token está en la URL: no debe viajar a terceros por Referer
    return respuesta

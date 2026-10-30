"""Vistas de cuenta: finas. Autentican, validan el esquema, llaman a UN servicio y responden.

Sin reglas de negocio, sin ORM y sin comprobar permisos con `if` propios (eso vive en servicios y políticas).
Todos los mensajes de error son genéricos: no revelan si una cédula existe (CA-05).
"""
from django.conf import settings
from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme, urlencode
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from config.endpoints import endpoint

from . import esquemas, servicios

MENSAJE_LOGIN = "No fue posible iniciar sesión. Verifique su cédula y su clave, o intente más tarde."
MENSAJE_LIMITE = "Demasiados intentos. Espere unos minutos antes de volver a intentarlo."
MENSAJE_CODIGO = "El código no es válido o ya venció. Solicite uno nuevo si hace falta."


def _limitado(request, error: servicios.LimiteExcedido):
    respuesta = render(request, "errores/429.html", status=429)
    respuesta["Retry-After"] = str(max(1, error.reintentar_en))
    return respuesta


def _diferida(request, error: servicios.OperacionDiferida):
    """Espera de 24 h tras cambiar el contacto: las operaciones sensibles quedan diferidas (Anexo F)."""
    respuesta = render(request, "cuentas/operacion_diferida.html", {"hasta": error.hasta}, status=423)
    return respuesta


def _desafio_de(request):
    """(token, respuesta) enviados por el formulario, o None."""
    return (request.POST.get("desafio_token", ""), request.POST.get("desafio_respuesta", "")) if request.method == "POST" else None


def _contexto_desafio(extra: dict | None = None) -> dict:
    """Si la detección activó el desafío adaptativo, se muestra una pregunta nueva; si no, nada cambia."""
    datos = dict(extra or {})
    if servicios.desafio_requerido():
        datos["desafio"] = servicios.nuevo_desafio()
    return datos


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


@endpoint("S02")
@require_http_methods(["GET", "POST"])
def solicitar_activacion(request):
    form = esquemas.SolicitudActivacionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            servicios.solicitar_activacion(form.cleaned_data["cedula"], _desafio_de(request))
        except servicios.LimiteExcedido as error:
            return _limitado(request, error)
        except servicios.DesafioRequerido:
            form.add_error(None, "Responda la verificación para continuar.")
            return render(request, "cuentas/solicitar_activacion.html", _contexto_desafio({"form": form}))
        # Respuesta idéntica exista o no la cédula.
        return render(request, "cuentas/activacion_enviada.html", {"cedula": form.cleaned_data["cedula"]})
    return render(request, "cuentas/solicitar_activacion.html", _contexto_desafio({"form": form}))


@endpoint("E02")
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


@endpoint("E03")
@require_http_methods(["GET", "POST"])
@never_cache
def ingresar(request):
    form = esquemas.IngresoForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            resultado = servicios.autenticar(request, form.cleaned_data["cedula"], form.cleaned_data["clave"], _desafio_de(request))
        except servicios.LimiteExcedido as error:
            return _limitado(request, error)
        except servicios.DesafioRequerido:
            form.add_error(None, "Responda la verificación para continuar.")
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
    return render(request, "cuentas/ingresar.html", _contexto_desafio({"form": form, "next": request.GET.get("next", "")}))


@endpoint("E04")
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


@endpoint("S03")
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


@endpoint("S04")
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


@endpoint("E05")
@require_POST
def salir(request):
    """Cierra la sesión actual, UNA de las propias (campo `sesion`) o TODAS (alcance=todas)."""
    form = esquemas.CerrarSesionForm(request.POST)
    if not form.is_valid():
        return redirect("cuentas:sesiones")
    alcance = form.cleaned_data["alcance"] or ("una" if form.cleaned_data["sesion"] else "actual")
    if not servicios.cerrar_sesiones(request, alcance, form.cleaned_data["sesion"]):
        raise Http404()  # misma respuesta para "no existe" y "no es suya"
    messages.info(request, "Sesión cerrada.")
    return redirect("cuentas:sesiones" if request.user.is_authenticated else "cuentas:ingresar")


# --- Sesiones y clave -------------------------------------------------------------------------------------------------------


@endpoint("S05")
@require_http_methods(["GET"])
def sesiones(request):
    return render(request, "cuentas/sesiones.html", {"sesiones": servicios.listar_sesiones(request)})


@endpoint("S07")
@require_http_methods(["GET", "POST"])
@never_cache
def cambiar_clave(request):
    form = esquemas.CambiarClaveForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        datos = form.cleaned_data
        try:
            servicios.cambiar_clave(request, datos["clave_actual"], datos["clave_nueva"])
        except servicios.OperacionDiferida as error:
            return _diferida(request, error)
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


@endpoint("S08")
@require_http_methods(["GET", "POST"])
def solicitar_recuperacion(request):
    form = esquemas.SolicitudRecuperacionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            servicios.solicitar_recuperacion(form.cleaned_data["cedula"], _desafio_de(request))
        except servicios.LimiteExcedido as error:
            return _limitado(request, error)
        except servicios.DesafioRequerido:
            form.add_error(None, "Responda la verificación para continuar.")
            return render(request, "cuentas/solicitar_recuperacion.html", _contexto_desafio({"form": form}))
        return render(request, "cuentas/recuperacion_enviada.html")  # mismo mensaje exista o no la cédula
    return render(request, "cuentas/solicitar_recuperacion.html", _contexto_desafio({"form": form}))


@endpoint("S09")
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


# --- E15: derechos del titular -------------------------------------------------------------------------------------------------------------


@endpoint("E15")
@never_cache
def solicitudes_derechos(request):
    form = esquemas.DerechosForm(request.POST if request.method == "POST" else None)
    try:
        if request.method == "POST" and form.is_valid():
            servicios.registrar_solicitud_derechos(request, form.cleaned_data["tipo"], form.cleaned_data["detalle"])
            messages.success(request, "Registramos su solicitud. Le responderemos dentro de los plazos legales.")
            return redirect("cuentas:solicitudes_derechos")
        solicitudes = servicios.listar_solicitudes_derechos(request.user)
    except servicios.OperacionDiferida as error:
        return _diferida(request, error)
    except servicios.ReautenticacionRequerida as error:
        destino = "cuentas:mfa_configurar" if error.configurar_mfa else "cuentas:reautenticar"
        return redirect(f"{reverse(destino)}?{urlencode({'next': reverse('cuentas:solicitudes_derechos')})}")
    except servicios.AccesoDenegado:
        return render(request, "errores/403.html", status=403)
    return render(request, "cuentas/solicitudes_derechos.html", {"form": form, "solicitudes": solicitudes})


# --- E19: auditoría (solo lectura) -------------------------------------------------------------------------------------------------------------


@endpoint("E19")
def auditoria(request):
    form = esquemas.AuditoriaForm(request.GET)
    if not form.is_valid():
        return render(request, "errores/404.html", status=400)
    d = form.cleaned_data
    try:
        pagina = servicios.consultar_auditoria(
            request.user, accion=d["accion"] or None, resultado=d["resultado"] or None, actor_id=d["actor"], desde=d["desde"], hasta=d["hasta"], pagina=d["pagina"] or 1,
        )
    except servicios.AccesoDenegado:
        return render(request, "errores/403.html", status=403)
    except servicios.ClaveRechazada:
        return render(request, "errores/404.html", status=400)
    return render(request, "cuentas/auditoria.html", {"pagina": pagina, "form": form})


# --- Soporte: datos técnicos enmascarados; NUNCA restablece claves por sí mismo -------------------------------------------------------------------


@endpoint("S20")
def ficha_tecnica(request, cedula):
    try:
        ficha = servicios.ficha_tecnica(request.user, cedula)
    except servicios.AccesoDenegado:
        return render(request, "errores/403.html", status=403)
    except servicios.NoEncontrado:
        return render(request, "errores/404.html", status=404)
    except servicios.LimiteExcedido as error:
        return _limitado(request, error)
    return render(request, "cuentas/ficha_tecnica.html", {"ficha": ficha, "cedula": cedula, "form": esquemas.SoporteRecuperacionForm()})


@endpoint("S21")
def recuperacion_soporte(request, cedula):
    form = esquemas.SoporteRecuperacionForm(request.POST)
    if not form.is_valid():
        return render(request, "errores/404.html", status=400)
    try:
        servicios.iniciar_recuperacion_presencial(request.user, cedula, form.cleaned_data["verificacion_presencial"])
    except servicios.AccesoDenegado:
        return render(request, "errores/403.html", status=403)
    except servicios.NoEncontrado:
        return render(request, "errores/404.html", status=404)
    except servicios.ClaveRechazada as error:
        for problema in error.problemas:
            messages.error(request, problema)
        return redirect(reverse("cuentas:ficha_tecnica", args=[cedula]))
    messages.success(request, "Se inició la recuperación: el enlace llegó al contacto registrado de la persona. Usted no puede verlo.")
    return redirect(reverse("cuentas:ficha_tecnica", args=[cedula]))


# --- Contacto y consentimiento (rectificación y oposición del titular) -------------------------------------------------------------------------------


@endpoint("S26")
@never_cache
def contacto(request):
    form = esquemas.ContactoCambioForm(request.POST if request.method == "POST" else None)
    try:
        if request.method == "POST" and form.is_valid():
            d = form.cleaned_data
            if d["paso"] == "solicitar":
                servicios.solicitar_cambio_contacto(request, d["campo"], d["valor"])
                messages.info(request, "Enviamos un código al contacto NUEVO. Escríbalo abajo para confirmar el cambio.")
            else:
                servicios.confirmar_cambio_contacto(request, d["codigo"])
                messages.success(request, "Contacto actualizado. Avisamos al contacto anterior; por seguridad, las operaciones sensibles esperan 24 horas.")
            return redirect("cuentas:contacto")
    except servicios.OperacionDiferida as error:
        return _diferida(request, error)
    except servicios.ReautenticacionRequerida as error:
        destino = "cuentas:mfa_configurar" if error.configurar_mfa else "cuentas:reautenticar"
        return redirect(f"{reverse(destino)}?{urlencode({'next': reverse('cuentas:contacto')})}")
    except servicios.ClaveRechazada as error:
        for problema in error.problemas:
            form.add_error(None, problema)
    except servicios.CodigoInvalido:
        form.add_error(None, "El código no es válido o ya venció.")
    except servicios.LimiteExcedido as error:
        return _limitado(request, error)
    return render(request, "cuentas/contacto.html", {"form": form})


@endpoint("S27")
@never_cache
def preferencias(request):
    form = esquemas.PreferenciasForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        servicios.actualizar_consentimiento_sms(request.user, form.cleaned_data["consentimiento_sms"])
        messages.success(request, "Preferencias guardadas.")
        return redirect("cuentas:preferencias")
    consiente, oposicion = servicios.preferencias_de_notificacion(request.user.id)
    return render(request, "cuentas/preferencias.html", {"form": esquemas.PreferenciasForm(initial={"consentimiento_sms": consiente}), "oposicion": oposicion})

from django.urls import path

from . import vistas

app_name = "cuentas"

urlpatterns = [
    path("activar/", vistas.solicitar_activacion, name="activar"),
    path("activar/confirmar/", vistas.confirmar_activacion, name="activar_confirmar"),
    path("ingresar/", vistas.ingresar, name="ingresar"),
    path("mfa/", vistas.verificar_mfa, name="mfa"),
    path("mfa/configurar/", vistas.configurar_mfa, name="mfa_configurar"),
    path("reautenticar/", vistas.reautenticar, name="reautenticar"),
    path("salir/", vistas.salir, name="salir"),
    path("sesiones/", vistas.sesiones, name="sesiones"),
    path("sesiones/<uuid:sesion_id>/revocar/", vistas.revocar_sesion, name="revocar_sesion"),
    path("clave/cambiar/", vistas.cambiar_clave, name="cambiar_clave"),
    path("recuperar/", vistas.solicitar_recuperacion, name="recuperar"),
    path("restablecer/<str:token>/", vistas.restablecer_clave, name="restablecer"),
]

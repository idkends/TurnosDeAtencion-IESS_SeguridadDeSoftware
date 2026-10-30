from django.urls import path

from . import vistas

app_name = "cuentas"

urlpatterns = [
    # Endpoints del Anexo D
    path("cuentas/activar", vistas.confirmar_activacion, name="activar_confirmar"),  # E02
    path("sesion", vistas.ingresar, name="ingresar"),  # E03
    path("sesion/segundo-factor", vistas.verificar_mfa, name="mfa"),  # E04
    path("sesion/cerrar", vistas.salir, name="salir"),  # E05
    path("mis-datos/solicitudes", vistas.solicitudes_derechos, name="solicitudes_derechos"),  # E15
    path("auditoria", vistas.auditoria, name="auditoria"),  # E19
    # Rutas de soporte (ver config/inventario.py)
    path("cuenta/activar/", vistas.solicitar_activacion, name="activar"),
    path("cuenta/mfa/configurar/", vistas.configurar_mfa, name="mfa_configurar"),
    path("cuenta/reautenticar/", vistas.reautenticar, name="reautenticar"),
    path("cuenta/sesiones/", vistas.sesiones, name="sesiones"),
    path("cuenta/clave/cambiar/", vistas.cambiar_clave, name="cambiar_clave"),
    path("cuenta/recuperar/", vistas.solicitar_recuperacion, name="recuperar"),
    path("cuenta/restablecer/<token:token>/", vistas.restablecer_clave, name="restablecer"),
]

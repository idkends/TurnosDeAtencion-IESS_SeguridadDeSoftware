"""URLconf de PRUEBA con una ruta que no está en el inventario (solo para probar que la prueba del inventario la detecta)."""
from django.http import HttpResponse
from django.urls import include, path

urlpatterns = [path("debug/volcado", lambda request: HttpResponse("secreto")), path("", include("config.urls"))]

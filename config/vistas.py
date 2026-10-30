"""Vistas transversales: inicio, salud y páginas de error genéricas (sin detalles técnicos)."""
from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET


@require_GET
def salud(request):
    return HttpResponse("ok", content_type="text/plain")


@require_GET
def inicio(request):
    # Placeholder de la etapa 1: la interfaz de turnos llega en la etapa 2.
    return render(request, "inicio.html")


def error_403(request, exception=None):
    return render(request, "errores/403.html", status=403)


def error_404(request, exception=None):
    return render(request, "errores/404.html", status=404)


def error_500(request):
    return render(request, "errores/500.html", status=500)

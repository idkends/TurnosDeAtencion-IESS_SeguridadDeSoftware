"""Genera `docs/endpoints.md` a partir de `config/inventario.py` (una sola fuente de verdad)."""
from config.inventario import ENTRADAS


def _fila(e) -> str:
    metodos = " / ".join(e.metodos)
    celdas = [e.id, f"{metodos} `{e.ruta}`", e.funcion, e.autenticacion, e.autorizacion, e.limites_texto, e.idempotencia_csrf, e.riesgos or "-"]
    return "| " + " | ".join(c.replace("|", "/") for c in celdas) + " |"


def generar() -> str:
    cabecera = "| ID | Método y ruta | Función | Autenticación | Autorización y objeto | Límites y validación | Idempotencia y CSRF | Riesgos y pruebas |\n|---|---|---|---|---|---|---|---|\n"
    oficiales = "\n".join(_fila(e) for e in ENTRADAS if not e.soporte)
    soporte = "\n".join(_fila(e) for e in ENTRADAS if e.soporte)
    return f"""# Inventario de endpoints

> **Generado** desde `config/inventario.py` con `python manage.py generar_inventario`. No se edita a mano: una prueba
> (`tests/seguridad/test_inventario.py`) falla si este archivo no coincide con el inventario o si existe una ruta que no
> está en él. Fuente del diseño: Anexo D de `docs/informe.pdf`.

Reglas comunes: respuestas de error genéricas, campos desconocidos rechazados (esquemas estrictos), CORS cerrado, toda ruta
que cambia estado exige un método distinto de GET y token CSRF, identificadores UUID en las rutas y el mismo error 404 para
un recurso inexistente o ajeno.

## Endpoints del Anexo D (E01-E21)

{cabecera}{oficiales}

## Rutas de soporte (no numeradas en el Anexo D)

Pantallas de formularios y los pasos del Anexo F que el prototipo necesita. Pasan por las mismas pruebas y límites.

{cabecera}{soporte}

## Notas de diseño

- **GET en rutas de formulario:** E02, E03, E04, E15 y E19 aceptan GET solo para mostrar el formulario o la lista; lo que cambia estado es siempre POST.
- **E14 (DELETE):** los formularios HTML no envían DELETE, así que aceptan POST con `_metodo=DELETE`; cualquier otro POST recibe 405.
- **E20 (GET que consume el token):** el inventario del informe lo define como GET. Por eso el token solo sirve para llegar a iniciar sesión (nunca abre datos) y caduca a las 24 h.
- **E18 lleva la cédula en la ruta** (según el Anexo D): el proxy no la registra completa en sus registros de acceso.
- **E21 /salud** solo responde a la red interna: el proxy la bloquea desde fuera.
"""

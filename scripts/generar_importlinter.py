"""Regenera `.importlinter` a partir de la lista de apps y de los módulos que existen en cada una.

Uso: python scripts/generar_importlinter.py   (cada vez que se agrega una app o un módulo de capa)
Así los contratos no se desfasan cuando el proyecto crece: la prueba de arquitectura exige que el archivo esté al día.
"""
import pathlib

RAIZ = pathlib.Path(__file__).resolve().parent.parent
# De la capa más alta a la más baja; los pares con "|" son hermanos independientes entre sí.
CAPAS = ["apps.derechos", "apps.turnos | apps.deteccion", "apps.delegaciones", "apps.cuentas", "apps.notificaciones | apps.integraciones", "apps.auditoria"]
APPS = sorted({a.strip().split(".")[1] for capa in CAPAS for a in capa.split("|")})
CAPA_VISTAS = ("vistas", "esquemas", "urls")
MODULOS_INTERNOS = ("servicios", "vistas", "esquemas", "urls", "modelos", "politicas", "middleware", "contexto")


def existe(app: str, modulo: str) -> bool:
    return (RAIZ / "apps" / app / f"{modulo}.py").exists()


def lineas(items) -> str:
    return "\n".join(f"    {i}" for i in items)


def generar() -> str:
    vistas = [f"apps.{a}.{m}" for a in APPS for m in CAPA_VISTAS if existe(a, m)] + ["config.vistas"]
    modelos_y_shims = [f"apps.{a}.{m}" for a in APPS for m in ("modelos", "models") if existe(a, m)]
    servicios = [f"apps.{a}.servicios" for a in APPS if existe(a, "servicios")]
    con_modelos = [f"apps.{a}.modelos" for a in APPS if existe(a, "modelos")]
    vistas_de_apps = [f"apps.{a}.vistas" for a in APPS if existe(a, "vistas")]
    partes = [
        """[importlinter]
root_packages =
    apps
    config
include_external_packages = True

# GENERADO por scripts/generar_importlinter.py (no se edita a mano).
# Nota: import-linter no admite subpaquetes externos (django.db, django.http...) como prohibidos; esas reglas
# las cubre la prueba de arquitectura por inspección de AST (tests/arquitectura).
# Las pruebas (apps/*/tests) preparan datos con el ORM y pueden cruzar apps: se excluyen de estos contratos.
# Todo el código de producción debe cumplirlos. Si se rompe una regla, `lint-imports` falla y la prueba de
# arquitectura (tests/arquitectura) falla con él.
""",
        f"""[importlinter:contract:vistas-sin-orm]
name = Capa 1: las vistas, esquemas y urls no importan modelos ni el ORM (solo llaman a un servicio)
type = forbidden
source_modules =
{lineas(vistas)}
forbidden_modules =
{lineas(modelos_y_shims + ["django.db"][:0])}
allow_indirect_imports = True
""",
        f"""[importlinter:contract:servicios-sin-http]
name = Capa 2: los servicios no dependen de vistas, urls ni del framework HTTP
type = forbidden
source_modules =
{lineas(servicios)}
forbidden_modules =
{lineas(vistas_de_apps + [f"apps.{a}.urls" for a in APPS if existe(a, "urls")])}
allow_indirect_imports = True
""",
        f"""[importlinter:contract:modelos-sin-servicios]
name = Capa 3: la persistencia no depende de servicios ni de vistas
type = forbidden
source_modules =
{lineas(con_modelos)}
forbidden_modules =
{lineas(servicios + vistas_de_apps)}
allow_indirect_imports = True
""",
        """[importlinter:contract:politicas-puras]
name = Autorización: las políticas son funciones puras (sin ORM, sin servicios, sin E/S de red)
type = forbidden
source_modules =
    apps.cuentas.politicas
forbidden_modules =
    apps.cuentas.modelos
    apps.cuentas.servicios
    apps.auditoria.servicios
    apps.integraciones.servicios
    apps.notificaciones.servicios
allow_indirect_imports = True
""",
        f"""[importlinter:contract:sentido-de-las-dependencias]
name = Las apps dependen en un solo sentido (derechos > turnos, deteccion > delegaciones > cuentas > notificaciones, integraciones > auditoria)
type = layers
layers =
{lineas(CAPAS)}
ignore_imports =
    apps.**.tests.** -> apps.**
""",
        """[importlinter:contract:config-sin-apps]
name = La infraestructura compartida (config) no importa ninguna app
type = forbidden
source_modules =
    config
forbidden_modules =
    apps
allow_indirect_imports = True
""",
    ]
    for dueña in APPS:
        otras = [a for a in APPS if a != dueña]
        fuentes = [f"apps.{o}.{m}" for o in otras for m in MODULOS_INTERNOS if existe(o, m)]
        fuentes += [f"apps.{o}.management" for o in otras if (RAIZ / "apps" / o / "management").exists()]
        prohibidos = [f"apps.{dueña}.{m}" for m in ("modelos", "models", "vistas", "esquemas", "urls") if existe(dueña, m)]
        if dueña == "cuentas":
            prohibidos += ["apps.cuentas.roles", "apps.cuentas.claves", "apps.cuentas.middleware", "apps.cuentas.contexto"]
        elif existe(dueña, "politicas"):
            prohibidos.append(f"apps.{dueña}.politicas")
        if dueña == "integraciones":
            prohibidos += [f"apps.integraciones.{m}" for m in ("adaptadores", "circuito", "puertos", "servicio_token", "sinteticos", "simulados")]
        if dueña == "notificaciones":
            prohibidos.append("apps.notificaciones.adaptadores")
        partes.append(
            f"""[importlinter:contract:solo-servicios-{dueña}]
name = Nadie fuera de {dueña} importa sus modelos, vistas ni piezas internas (solo su servicios.py publico)
type = forbidden
source_modules =
{lineas(fuentes)}
forbidden_modules =
{lineas(prohibidos)}
allow_indirect_imports = True
"""
        )
    return "\n".join(partes)


if __name__ == "__main__":
    (RAIZ / ".importlinter").write_text(generar(), encoding="utf-8", newline="\n")
    print(".importlinter regenerado")

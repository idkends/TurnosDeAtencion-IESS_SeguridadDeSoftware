# Demostraciones «antes y después» (§10.2 del informe, Anexo G)

Cada demostración lanza **el mismo ataque** contra dos entornos y muestra lo que pasa:

| Entorno | Rama | URL | Proyecto Compose |
|---|---|---|---|
| **ANTES** — versión intencionalmente insegura | `lab/inseguro` | <https://localhost:9443> | `iess-lab` (carpeta hermana `../iess-lab`) |
| **DESPUÉS** — versión real | `main` | <https://localhost:8443> | `iess-turnos` |

> ⚠ **Las fallas de `lab/inseguro` son INTENCIONALES** y existen solo para la clase. Están marcadas en el código con `⚠ INSEGURO A PROPÓSITO`,
> en las migraciones `0004_lab_inseguro` y en esta tabla. Esa rama **nunca se integra a `main`**. Úsese únicamente en el equipo local, con datos sintéticos.

## Cómo ejecutarlas

```sh
docker compose up -d --build && docker compose --profile utilidades run --rm sembrar   # main (8443), si no está levantado
bash scripts/lab_levantar.sh      # (o: make lab) crea ../iess-lab desde lab/inseguro, con su propio .env, base de datos y puertos 9443/19080
bash scripts/demos/demo_a.sh      # … demo_b.sh … demo_f.sh      (o: make demos)
bash scripts/lab_bajar.sh         # (o: make lab-bajar) borra el entorno inseguro
```

Resultado esperado de cada `demo_X.sh`: **«✔ el ataque funciona en lab/inseguro y FALLA en main»**; si no es así, sale con error.
Salida real de una ejecución completa: ver «Resultados verificados» al final.

Detalle del arnés: antes de cada demo se reinician los contadores de Redis y se **pausa la tarea de detección** del entorno atacado (si no, la regla D06
bloquearía por IP al propio presentador a mitad de la demo; se reanuda al terminar). Los ataques los ejecuta `scripts/demos/ataques.py` con un navegador
mínimo (`cliente.py`) que no sigue redirecciones para mostrar el código exacto del servidor.

## Las seis

| Demo | Módulo / riesgo | Falla intencional en `lab/inseguro` | Ataque (idéntico en ambos) | ANTES (lab) | DESPUÉS (main) |
|---|---|---|---|---|---|
| **A** `demo_a.sh` | M01 · R05, R18 | La cuenta nueva usa la **fecha de nacimiento** como clave inicial (`precargar_afiliado`/`sembrar`) y el login responde «Cédula no registrada» o «Clave incorrecta» (`autenticar`) | Con la fecha de nacimiento de una víctima intenta iniciar sesión; compara el mensaje de una cédula inexistente con el de una existente | Entra a la cuenta; los mensajes **delatan** qué cédulas existen | HTTP 200 con el mismo error genérico siempre: la fecha no autentica y el mensaje es idéntico |
| **B** `demo_b.sh` | M03 · R15 | `puede_ver_turno` devuelve verdadero para cualquiera (sin autorización por objeto) | Un usuario B pide `/turnos/{id}` de un turno de A | HTTP 200 y ve el nombre de A | HTTP 404, igual que un turno inexistente (el informe lo llama «403»; el diseño usa 404 idéntico para no revelar ni que existe) |
| **C** `demo_c.sh` | M04 · R13 | Sin bloqueo de fila (`select_for_update`) y sin el índice único `uq_turno_franja` (migración `turnos/0004_lab_inseguro`); una pausa de 0,4 s ensancha la carrera | 8 personas reservan **a la vez** la misma franja | Las 8 obtienen turno (8 turnos para 1 franja) | Exactamente 1 turno y 7 respuestas 409 |
| **D** `demo_d.sh` | M07 · R22 | Migración `auditoria/0004_lab_inseguro`: sin disparador de solo adición y con `UPDATE, DELETE` concedido a `iess_app` | Con el rol de la aplicación hace `UPDATE` y `DELETE` sobre un evento de auditoría | `UPDATE 1` / `DELETE 1`: la huella desaparece | `permission denied`: la base rechaza (permisos + disparador) |
| **E** `demo_e.sh` | M08 · R07 | Si la afiliación no responde, `reservar` da por buena la cobertura y sigue a la agenda | Reserva una persona **no afiliada** mientras se apaga `afiliacion_sim` | Turno «**Reservado**»: se inventó la cobertura | Turno «**Pendiente**»: nunca se afirma lo que no se pudo comprobar (RT-10) |
| **F** `demo_f.sh` | M01 · R20 | `ALLOWED_HOSTS=*` en el `.env` del laboratorio y el enlace de recuperación se arma con la cabecera `Host` | Pide recuperar la cuenta de una víctima con `Host: atacante.example` | El correo de la víctima trae `https://atacante.example/cuenta/restablecer/…` | HTTP 400: el Host falso se rechaza; los enlaces usan el dominio fijo (CA-16) |

### Qué enseñar con cada una

- **A** — La seguridad de una cuenta no puede depender de datos públicos o conocidos; los mensajes y tiempos idénticos impiden enumerar afiliados.
- **B** — Autorización por objeto: el servidor decide si *ese* turno es *de ese* usuario; un 404 idéntico no revela existencia.
- **C** — Una regla de negocio importante vive también **en la base de datos**; el código solo no basta bajo concurrencia.
- **D** — La auditoría de solo adición se impone con permisos y disparadores, no con la buena voluntad. *Observación honesta:* en la salida de la demo, `verificar_auditoria`
  sigue diciendo «íntegra» en el entorno inseguro porque el atacante borró **el último** eslabón de la cadena; una cadena de hashes no detecta un truncado
  del final. Por eso el informe exige además **copia externa** de la auditoría (ver `docs/limitaciones.md`).
- **E** — Ante un tercero caído, el sistema debe degradar a «pendiente», nunca asumir.
- **F** — El dominio de los enlaces sale de la configuración, no de lo que mande el cliente.

## Resultados verificados

Ejecución del 2026-10-30 contra los dos entornos reales (ver también las pruebas automáticas que cubren cada punto en `docs/trazabilidad.md`):

| Demo | lab/inseguro | main | Resultado |
|---|---|---|---|
| A | entró con `19920505`; mensajes distintos | HTTP 200 genérico ×3; mensaje idéntico | ✔ |
| B | HTTP 200 y datos de la víctima | HTTP 404 | ✔ |
| C | 8 turnos para una franja | 1 turno, 7 × 409 | ✔ |
| D | `UPDATE 1`, `DELETE 1` | `permission denied` | ✔ |
| E | «Reservado» sin cobertura | «Pendiente» | ✔ |
| F | enlace a `atacante.example` | HTTP 400 | ✔ |

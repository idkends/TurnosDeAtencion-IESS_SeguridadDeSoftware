# Inventario de endpoints

> **Generado** desde `config/inventario.py` con `python manage.py generar_inventario`. No se edita a mano: una prueba
> (`tests/seguridad/test_inventario.py`) falla si este archivo no coincide con el inventario o si existe una ruta que no
> está en él. Fuente del diseño: Anexo D de `docs/informe.pdf`.

Reglas comunes: respuestas de error genéricas, campos desconocidos rechazados (esquemas estrictos), CORS cerrado, toda ruta
que cambia estado exige un método distinto de GET y token CSRF, identificadores UUID en las rutas y el mismo error 404 para
un recurso inexistente o ajeno.

## Endpoints del Anexo D (E01-E21)

| ID | Método y ruta | Función | Autenticación | Autorización y objeto | Límites y validación | Idempotencia y CSRF | Riesgos y pruebas |
|---|---|---|---|---|---|---|---|
| E01 | GET `/disponibilidad` | Franjas libres por especialidad, establecimiento y fecha | anonimo (sin datos personales) | No aplica | Parámetros en lista blanca; paginación; caché 30 s; 60 por minuto y por IP | Lectura: no aplica | R21, R12, T09 |
| E02 | GET / POST `/cuentas/activar` | Activar la cuenta con el código de un solo uso (GET muestra el formulario) | credenciales (código, Anexo F) | El código pertenece a esa cédula | 5 intentos por código; mensajes genéricos; límites por IP | Token CSRF | R05, R18, T03 |
| E03 | GET / POST `/sesion` | Iniciar sesión con cédula y clave (GET muestra el formulario) | credenciales | No aplica | Bloqueo progresivo; respuesta idéntica ante cédula inexistente; 10 por minuto y por IP y por dispositivo; desafío adaptativo si la detección lo activa | Token CSRF | R05, R18, R19, T03 |
| E04 | GET / POST `/sesion/segundo-factor` | Verificar TOTP o código de recuperación | sesion parcial (tras la clave) | La sesión parcial es de esa cuenta | 5 intentos y bloqueo; 10 por minuto y por IP | Token CSRF | R05, T03 |
| E05 | POST `/sesion/cerrar` | Cerrar sesión (la actual, una de las propias o todas) | sesion | Propia | - | Token CSRF | R19, T03 |
| E06 | GET `/turnos` | Lista de turnos del titular o de un representado | sesion | Propios o con delegación vigente; el titular se valida en el servidor | Paginación; filtros en lista blanca | Lectura | R15, R04, T13, T02 |
| E07 | GET `/turnos/{id}` | Detalle de un turno | sesion | El turno es del titular o de un representado con delegación vigente; id no secuencial (UUID); 404 igual para inexistente y ajeno | Formato del id validado | Lectura | R15, T13 |
| E08 | POST `/turnos` | Reservar un turno (RT-01 a RT-12) | sesion | Titular o representante con delegación; reglas de negocio en el servidor | Esquema estricto (campos desconocidos se rechazan); 10 por hora y por cuenta; cuerpo máximo 64 KB | Clave de idempotencia obligatoria; token CSRF | R12, R13, T07, T16 |
| E09 | POST `/turnos/{id}/confirmar` | Confirmar dentro de las 24 horas | sesion | Objeto del titular o representante | Solo desde RESERVADO | Idempotente; token CSRF | R14, T17 |
| E10 | POST `/turnos/{id}/cancelar` | Cancelar hasta 2 horas antes (o deshacer en 10 minutos) | sesion | Objeto del titular o representante | Confirmación explícita | Idempotente; token CSRF | R14, T17 |
| E11 | POST `/turnos/{id}/reprogramar` | Reprogramar (máximo 2 veces) | sesion | Objeto del titular o representante | Contador por turno | Clave de idempotencia; token CSRF | R14, R13, T17 |
| E12 | POST `/turnos/{id}/apelar` | Apelar una restricción por inasistencias | sesion | Restricción del propio titular | Texto limitado y escapado | Token CSRF | R14, T17 |
| E13 | POST `/delegaciones` | Crear una delegación | sesion con segundo factor reciente | Solo el titular o el tutor verificado | Máximo 5 representados por representante | Token CSRF | R04, T02 |
| E14 | DELETE / POST `/delegaciones/{id}` | Revocar una delegación (formulario HTML: POST con _metodo=DELETE) | sesion | Titular o representante | - | Idempotente; token CSRF | R04, T02 |
| E15 | GET / POST `/mis-datos/solicitudes` | Acceso, rectificación, eliminación u oposición (GET lista las propias) | sesion con segundo factor reciente | Solo el titular | Registro de la solicitud; respuesta en 10 y 15 días | Token CSRF | N03, T11 |
| E16 | POST `/interno/turnos/{id}/asistencia` | Recepción registra asistió, no asistió o justificación | funcionario con segundo factor | Rol recepción y misma unidad | Solo adición; el cambio queda auditado | Idempotente; token CSRF | R14, R22, T04, T11b |
| E17 | GET `/interno/agenda` | Agenda de la unidad | funcionario con segundo factor | Rol y unidad; sin datos clínicos | Paginación; sin exportación masiva | Lectura | R06, T04 |
| E18 | GET `/interno/afiliados/{cedula}` | Consulta de contacto por recepción o soporte | funcionario con segundo factor | Datos enmascarados; completos solo con justificación registrada | Máximo 30 consultas por hora y por funcionario | Lectura; auditada | R06, R08, T04, T06 |
| E19 | GET `/auditoria` | Consulta de registros | auditor y DPD con segundo factor | Solo lectura; sin secretos | Filtros en lista blanca; paginación | Lectura | R22, T11b |
| E20 | GET `/enlaces/{token}` | Confirmación desde un aviso | token de un solo uso | El token pertenece a un turno; no abre datos | Caduca en 24 h; invalidado tras el uso | Redirige a iniciar sesión | R03, T05 |
| E21 | GET `/salud` | Verificación de estado del servicio | solo red interna (el proxy lo bloquea desde fuera) | No aplica | Sin datos ni versiones | Lectura | R09, T08 |

## Rutas de soporte (no numeradas en el Anexo D)

Pantallas de formularios y los pasos del Anexo F que el prototipo necesita. Pasan por las mismas pruebas y límites.

| ID | Método y ruta | Función | Autenticación | Autorización y objeto | Límites y validación | Idempotencia y CSRF | Riesgos y pruebas |
|---|---|---|---|---|---|---|---|
| S01 | GET `/` | Página de inicio | anonimo | No aplica | - | Lectura | - |
| S02 | GET / POST `/cuenta/activar/` | Solicitar el código de activación (Anexo F) | anonimo | El código va solo al contacto registrado en afiliación | 5 solicitudes por hora por cédula y 20 por IP; respuesta idéntica exista o no la cédula | Token CSRF | R05, R18, CA-04, CA-05 |
| S03 | GET / POST `/cuenta/mfa/configurar/` | Configurar el segundo factor (TOTP) | sesion o sesion parcial | Propia; no se puede reemplazar un factor con solo la clave | 10 por minuto y por IP | Token CSRF | R05, T03 |
| S04 | GET / POST `/cuenta/reautenticar/` | Confirmar clave y segundo factor para una operación sensible | sesion | Propia | 10 por minuto y por IP; alimenta el bloqueo | Token CSRF | R05, R19 |
| S05 | GET `/cuenta/sesiones/` | Listar las sesiones propias | sesion | Propias | - | Lectura | R19 |
| S07 | GET / POST `/cuenta/clave/cambiar/` | Cambiar la clave (cierra todas las sesiones) | sesion con segundo factor reciente | Propia | - | Token CSRF | R05, R19 |
| S08 | GET / POST `/cuenta/recuperar/` | Pedir el enlace de recuperación de clave (Anexo F) | anonimo | El enlace va solo al contacto registrado | 5 por hora por cédula y 20 por IP; respuesta idéntica | Token CSRF | R03, R18, CA-05, CA-16 |
| S09 | GET / POST `/cuenta/restablecer/{token}/` | Elegir una clave nueva con el token | token de un solo uso (128 bits, 15 min) | El token pertenece a esa cuenta; con 2.º factor si lo tiene | Un solo uso consumido en transacción | Token CSRF | R03, R19, CA-06 |
| S10 | GET `/delegaciones` | Ver y gestionar las delegaciones propias (formulario de alta) | sesion | Propias | - | Lectura | R04 |
| S11 | GET `/seguridad/alertas` | Panel de alertas de seguridad (reglas D01-D18) | funcionario con segundo factor | Auditor/DPD o administración de TI | Paginación; filtros en lista blanca | Lectura | R23, T12 |
| S12 | POST `/seguridad/alertas/{id}/reconocer` | Reconocer una alerta (revisión posterior) | funcionario con segundo factor | Auditor/DPD o administración de TI | Nota de hasta 300 caracteres | Idempotente; token CSRF | R23 |
| S13 | GET `/mis-datos/exportar` | Derecho de acceso: descargar los propios datos en JSON | sesion con segundo factor reciente | Solo el titular | Una descarga auditada | Lectura | N03, T11 |
| S14 | GET `/derechos/solicitudes` | Solicitudes de derechos de los titulares | funcionario con segundo factor | Auditor/DPD | Paginación | Lectura | N03 |
| S15 | POST `/derechos/solicitudes/{id}/atender` | Atender una solicitud (acceso, rectificación, oposición o eliminación) | funcionario con segundo factor | Auditor/DPD | Eliminación: anonimiza y ordena a los encargados (3 y 5 días) | Idempotente; token CSRF | N03, T10, T11 |
| S16 | GET `/exportaciones` | Listar y solicitar exportaciones puntuales | funcionario con segundo factor | Auditor/DPD y TI | Sin exportación masiva | Lectura | R06, T04 |
| S17 | POST `/exportaciones` | Solicitar una exportación puntual de auditoría | funcionario con segundo factor | Auditor/DPD | Motivo escrito; rango de hasta 31 días; tope de filas | Token CSRF | R06, T04 |
| S18 | POST `/exportaciones/{id}/aprobar` | Aprobar la exportación (segunda persona) | funcionario con segundo factor | Auditor/DPD o TI, distinto de quien la pidió | La base impide la autoaprobación | Idempotente; token CSRF | R06, T04 |
| S19 | GET `/exportaciones/{id}/descargar` | Descargar la exportación aprobada (una sola vez) | funcionario con segundo factor | Solo quien la solicitó | Vigencia de 24 h | Lectura; entrega única auditada | R06, T04 |
| S20 | GET `/soporte/cuentas/{cedula}` | Datos técnicos de una cuenta (enmascarados) | funcionario con segundo factor | Soporte | Máximo 30 consultas por hora y por funcionario | Lectura; auditada | R06, R08, T04 |
| S21 | POST `/soporte/cuentas/{cedula}/recuperacion` | Iniciar la recuperación con verificación presencial | funcionario con segundo factor | Soporte | El enlace va solo al contacto registrado | Token CSRF | R05, R20 |
| S22 | GET `/oferta/franjas` | Franjas de la unidad | funcionario con segundo factor | Administración de oferta de SU unidad | Paginación | Lectura | R06 |
| S23 | POST `/oferta/franjas` | Crear una jornada de franjas | funcionario con segundo factor | Administración de oferta de SU unidad | Duración de 10 a 120 min; solo dentro de la ventana de reserva | Token CSRF | R06 |
| S24 | POST `/oferta/franjas/{id}/cerrar` | Cerrar una franja (la institución cancela el turno con aviso) | funcionario con segundo factor | Administración de oferta de SU unidad | - | Idempotente; token CSRF | R06, R14 |
| S25 | POST `/interno/afiliados/{cedula}/emergencia` | Acceso excepcional (de emergencia) a datos completos | funcionario con segundo factor | Recepción o profesional de una unidad | Motivo obligatorio; 5 por hora; aviso inmediato; revisión posterior | Token CSRF | R06, T04 |
| S26 | GET / POST `/mis-datos/contacto` | Cambiar correo o teléfono (rectificación) | sesion con segundo factor reciente | Propio | Código al contacto nuevo; aviso al anterior; espera de 24 h para operaciones sensibles | Token CSRF | R05, R20, N03 |
| S27 | GET / POST `/mis-datos/preferencias` | Consentimiento de SMS (y su retiro) | sesion | Propio | - | Token CSRF | N03 |

## Notas de diseño

- **GET en rutas de formulario:** E02, E03, E04, E15 y E19 aceptan GET solo para mostrar el formulario o la lista; lo que cambia estado es siempre POST.
- **E14 (DELETE):** los formularios HTML no envían DELETE, así que aceptan POST con `_metodo=DELETE`; cualquier otro POST recibe 405.
- **E20 (GET que consume el token):** el inventario del informe lo define como GET. Por eso el token solo sirve para llegar a iniciar sesión (nunca abre datos) y caduca a las 24 h.
- **E18 lleva la cédula en la ruta** (según el Anexo D): el proxy no la registra completa en sus registros de acceso.
- **E21 /salud** solo responde a la red interna: el proxy la bloquea desde fuera.

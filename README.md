# Saint · Plataforma de agentes de IA para WhatsApp e Instagram

Plataforma multi-negocio (multi-tenant) de agentes de IA para atención al cliente.
El diseño completo está en el documento de la Fase 1.

## Estado

| Fase | Contenido | Estado |
|------|-----------|--------|
| 1 | Diseño (arquitectura, esquema, riesgos) | Lista |
| 2 | Modelos SQLAlchemy, migración Alembic, RLS y pruebas | Lista (PR #1) |
| 3.1 | App FastAPI mínima con `/health` | Lista (PR #2) |
| 3.2 | Evolution API en Docker y cliente en Python | Lista (PR #3) |
| 3.3 | Registrar un número en Saint (cifrado, webhook) | Lista (PR #4) |
| 3.4 | Recibir los avisos de Evolution y guardarlos en `webhook_events` | Lista (PR #5) |
| 3.5 | Cola ingest (ARQ): el worker procesa cada aviso, con reintentos | Lista (PR #6) |
| 3.6 | Cada aviso de mensaje se convierte en contacto, conversación y mensaje | Lista (PR #7) |
| 3.7 | Estado de conexión del número y prueba de punta a punta | Lista (PR #8) |
| 4.1 | Enviar mensajes: cola de salida, reintentos y confirmación | Lista (PR #9) |
| 4.2 | El bot espera 15 s a que el cliente termine y contesta (eco, sin IA) | Lista (PR #10) |
| 4.3 | Respuesta con IA (Claude Haiku 5.5), agente del negocio y registro en `llm_runs` | Lista (PR #11) |
| 4.4 a 4.8 | Conocimiento, RAG, pasar a humano, notas de voz y prueba final | Pendientes (falta la clave de OpenRouter) |
| 5.1 | Inicio de sesión del panel: contraseñas argon2, pase JWT de 15 min y sesión de 7 días | Lista (PR #13) |
| 5.2 | Negocio, miembros y roles; invitar con enlace de un solo uso | Lista (PR #14) |
| 5.3 | Números de WhatsApp desde el panel: conectar con QR, estado, grupos, desconectar | Lista (PR #15) |
| 5.4 | Bandeja de conversaciones: leer, contestar como persona, tomar y devolver al bot | Lista (PR #16) |
| 5.5 | Instrucciones del agente con historial de versiones, y horario de atención | Esta rama |

## Estructura

```
.
├── docker-compose.yml          PostgreSQL 16 + pgvector, Redis y Evolution API v2.3.7
├── docker/postgres/            Scripts que crean el usuario de la app y la base de Evolution
├── .env.example                Variables de entorno (copiar a .env)
└── backend/
    ├── pyproject.toml          Dependencias (se instalan con uv)
    ├── alembic.ini             Configuración de migraciones
    ├── app/
    │   ├── main.py             Crea la app FastAPI (conexiones al arrancar y apagar)
    │   ├── api/health.py       GET /health: estado de PostgreSQL y Redis
    │   ├── api/webhooks.py     POST /webhooks/evolution/{public_id}: avisos de Evolution
    │   ├── api/auth.py         /auth/...: entrar, renovar, elegir negocio, quién soy, salir, aceptar invitación
    │   ├── api/tenant.py       /tenant/...: datos del negocio, miembros, roles e invitaciones
    │   ├── api/numbers.py      /numbers/...: conectar números con QR, estado, grupos, borrar
    │   ├── api/conversations.py  /conversations/...: bandeja, mensajes, contestar, tomar, devolver
    │   ├── api/agent.py        /agent y /hours: instrucciones del bot, versiones y horario
    │   ├── core/config.py      Lee la configuración del .env
    │   ├── core/redis.py       Conexión a Redis
    │   ├── core/queue.py       Cola ingest (ARQ): nombre y cómo encolar
    │   ├── core/security.py    Cifrado (Fernet) y huellas de secretos
    │   ├── core/auth_tokens.py Contraseñas (argon2) y pases de acceso (JWT)
    │   ├── services/channel_registration.py  Registrar un número y su estado
    │   ├── services/webhook_ingest.py  Validar el secreto y guardar cada aviso
    │   ├── services/webhook_processing.py  Procesar avisos: manejadores y reintentos
    │   ├── services/message_ingest.py  Aviso de mensaje -> contacto, conversación, mensaje
    │   ├── services/connection_events.py  Aviso de conexión -> estado del número
    │   ├── services/outbound.py  Cola de salida: guardar y enviar mensajes por Evolution
    │   ├── services/bot_reply.py  Esperar 15 s y contestar al cliente
    │   ├── services/agents.py  Agente del negocio e historial que se le manda a la IA
    │   ├── services/auth.py    Sesiones del panel: entrar, renovar, cerrar, freno a intentos
    │   ├── services/members.py Qué puede hacer cada rol, miembros e invitaciones
    │   ├── services/audit.py   Registro de quién hizo qué (audit_logs)
    │   ├── services/numbers.py Números del negocio desde el panel y quién puede tocarlos
    │   ├── services/inbox.py   Bandeja: listar, leer, contestar, tomar, devolver, asignar, cerrar
    │   ├── services/agent_settings.py  Instrucciones del agente (con versiones) y horario
    │   ├── services/hours.py   Validar el horario y saber si el negocio está abierto
    │   ├── integrations/llm.py  Cliente de Claude (Anthropic)
    │   ├── workers/ingest.py   Worker ARQ de la cola ingest (+ barrido cada minuto)
    │   ├── integrations/evolution/  Cliente de Evolution API (instancias, QR, estado)
    │   ├── scripts/evolution_qr.py  Prueba manual: conectar un WhatsApp con QR
    │   ├── scripts/registrar_numero.py  Registrar el número de un negocio en Saint
    │   ├── scripts/enviar_mensaje.py  Prueba manual: enviar un WhatsApp desde Saint
    │   ├── scripts/crear_usuario.py  Crear un usuario del panel y darle acceso a un negocio
    │   ├── db/base.py          Clase Base, convención de nombres y piezas comunes
    │   ├── db/ids.py           Generador de UUID v7
    │   ├── db/session.py       Conexión y sesiones con el negocio activo (RLS)
    │   └── models/             Un archivo por tabla (20) + enums.py
    ├── migrations/
    │   ├── env.py              Cómo se conecta Alembic
    │   └── versions/0001_...   Esquema inicial: tablas, índices, ENUM y RLS
    └── tests/                  Pruebas de migración, restricciones y RLS
```

## Puesta en marcha (Windows, macOS o Linux)

Requisitos: Docker Desktop y [uv](https://docs.astral.sh/uv/) (instala Python 3.12+ solo).

```bash
cp .env.example .env            # en Windows: copy .env.example .env
# Edita .env y cambia las dos contraseñas (deben coincidir en las URLs).

docker compose up -d postgres redis

cd backend
uv sync                         # crea .venv e instala las dependencias
uv run alembic upgrade head     # crea las 19 tablas
uv run pytest                   # corre las pruebas (crea y borra la base saint_test)
uv run uvicorn app.main:app --reload   # arranca la API en http://localhost:8000
```

Con la API corriendo, abre http://localhost:8000/health (debe decir `"status":"ok"`)
y http://localhost:8000/docs para ver la documentación interactiva (solo en desarrollo).

Comandos útiles:

```bash
uv run alembic downgrade base   # deshace todo (¡borra los datos!)
uv run alembic upgrade head --sql   # muestra el SQL sin ejecutarlo
uv run alembic check            # avisa si los modelos cambiaron y falta una migración
uv run ruff check . && uv run ruff format .   # revisión y formato del código
```

## Conectar un WhatsApp de prueba (Evolution API)

```bash
docker compose up -d                       # levanta también Evolution en el puerto 8080
cd backend
uv run python -m app.scripts.evolution_qr prueba-andre
```

El script guarda el QR en `qr-prueba-andre.png`. Ábrelo y escanéalo desde WhatsApp >
Dispositivos vinculados. Cuando diga "Conectado." el número quedó vinculado a Evolution.
Para desvincularlo y borrarlo: `uv run python -m app.scripts.evolution_qr prueba-andre --borrar`.

Si ya habías levantado PostgreSQL antes de este bloque, la base `evolution` no existe
todavía (los scripts de `docker/postgres` solo corren con un volumen nuevo). En desarrollo,
lo más simple es `docker compose down -v` (borra los datos locales) y volver a levantar.

## Registrar el número de un negocio

```bash
uv run python -m app.scripts.registrar_numero "Panadería Prueba"
```

Crea el negocio si no existe, crea el número en Evolution con su webhook apuntando a
Saint, lo guarda en `channel_instances` (token cifrado y solo la huella del secreto del
webhook) y espera a que escanees el QR. Necesita `FERNET_KEY` en el `.env`.

## Recibir los avisos de Evolution (webhook)

Evolution manda cada mensaje y cambio de estado a
`POST /webhooks/evolution/{public_id}` con la cabecera `X-Saint-Webhook-Secret`. Saint
comprueba el secreto, guarda el aviso tal cual en `webhook_events` (sin la `apikey`) y
responde enseguida; si Evolution reintenta un aviso que ya estaba, no se duplica. Todavía
no se procesa: eso llega con la cola ingest (3.5).

Para probarlo, la API tiene que escuchar en todas las interfaces, porque Evolution la
llama desde dentro de Docker (`host.docker.internal:8000`):

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Escribe al número conectado desde otro teléfono y mira la tabla `webhook_events`.

## Worker de la cola ingest

Cada aviso guardado se encola en Redis (cola `saint:ingest`). El worker lo toma, llama al
manejador de su tipo y lo marca `processed`; si falla, lo reintenta (10 s, 20 s, 40 s...)
y tras 5 intentos lo deja en `failed` con el error. Cada minuto hace un barrido por si
algún aviso no se pudo encolar (por ejemplo, Redis estaba caído). Los mensajes se guardan
en `contacts`, `conversations` y `messages`, y los avisos de conexión actualizan el
estado del número en `channel_instances` (conectado, desconectado o bloqueado).

En otra terminal, junto a la API:

```bash
uv run arq app.workers.ingest.WorkerSettings
```

## Enviar mensajes (cola de salida)

La tabla `messages` también es la cola de salida. Un mensaje saliente se guarda con estado
`queued`, el worker lo toma (`sending`), se lo pasa a Evolution y lo marca `sent` con el id
de WhatsApp; después los avisos `messages.update` lo pasan a `delivered` y `read`.

- Número sin WhatsApp (Evolution responde 4xx): `failed`, sin reintentar.
- Evolution caído o error 5xx, o el número desconectado: se reintenta (hasta 5 veces).
- Evolution no responde a tiempo: se queda en `sending` y **no** se reintenta, para no
  mandarlo dos veces. El aviso `send.message` de Evolution confirma que sí salió.
- Si Redis estaba caído al encolar, el barrido de cada minuto lo encuentra.

Prueba manual, con la API, Evolution y el worker en marcha:

```bash
uv run python -m app.scripts.enviar_mensaje panaderia-prueba "+58 412 1234567" "Hola"
```

## Respuesta del bot (espera de 15 s)

Cuando un cliente escribe, el worker programa la respuesta para dentro de 15 s. Si el
cliente manda otro mensaje antes, la espera vuelve a empezar; si no para de escribir, se
contesta igual a los 2 minutos. Luego el bot lee todos los mensajes pendientes juntos y
contesta una sola vez.

La respuesta la escribe Claude Haiku 5.5 con las instrucciones del agente del negocio
(tabla `agents`; si no hay ninguno se crea uno básico) y los últimos 30 mensajes de la
conversación. Cada llamada queda en `llm_runs` con sus tokens. Si el cliente escribe
mientras la IA está respondiendo, esa respuesta se descarta y se contesta todo junto
después. Sin `ANTHROPIC_API_KEY` en el `.env`, el bot responde con un eco
("Recibí: ...").

- No contesta si la conversación la atiende una persona (`mode` distinto de `bot`), en
  grupos, ni a mensajes de hace más de una hora.
- Para probar sin molestar a clientes reales, pon en el `.env`
  `BOT_TEST_PHONES=+58 412 1234567` (varios separados por coma): solo contestará a esos.
- La migración 0005 da por atendidos los mensajes que llegaron antes del bot.

## Inicio de sesión del panel

La API ya sabe quién entra al panel; la pantalla se programa en la Fase 6 (el diseño
aprobado está en el canvas "Saint · Panel: inicio de sesión"). Hace falta `JWT_SECRET`
en el `.env` (genera una con `python -c "import secrets; print(secrets.token_urlsafe(48))"`).

```bash
cd backend
uv run python -m app.scripts.crear_usuario andre@saint.ai "Andre" --negocio panaderia-prueba
```

La contraseña se pide en la terminal (mínimo 10 caracteres) y no se ve al escribirla.

| Ruta | Qué hace |
| --- | --- |
| `POST /auth/login` | Correo y contraseña. Devuelve el pase de acceso (15 min) y deja la cookie de renovación (7 días) |
| `POST /auth/refresh` | Con la cookie, da un pase nuevo y cambia la cookie (la vieja deja de servir) |
| `POST /auth/select-tenant` | Elige el negocio con el que se trabaja (si la persona tiene varios) |
| `GET /auth/me` | Quién soy, mis negocios y el negocio activo |
| `POST /auth/logout` | Cierra la sesión de este navegador |

- Con un solo negocio, queda elegido al entrar. Con varios, el panel muestra la pantalla
  de elegir negocio.
- 5 contraseñas malas seguidas con un correo (o 20 desde una IP) bloquean 15 minutos.
- Si alguien usa una cookie de renovación ya usada (copiada), se cierran todas las
  sesiones de ese usuario.
- Con la API en marcha, `http://127.0.0.1:8000/docs` permite probar las rutas.

## Negocio, miembros y roles

Rutas del negocio que la persona eligió al entrar (necesitan el pase de acceso):

| Ruta | Qué hace | Quién puede |
| --- | --- | --- |
| `GET /tenant` | Datos del negocio y mi rol | Todos |
| `PATCH /tenant` | Cambiar nombre, país o zona horaria | Dueño y administrador |
| `GET /tenant/members` | Quiénes tienen acceso y con qué rol | Todos |
| `PATCH /tenant/members/{user_id}` | Cambiar el rol de alguien | Dueño (a todos), administrador (solo agentes) |
| `DELETE /tenant/members/{user_id}` | Quitar el acceso (con el propio id: salir del negocio) | Igual que arriba; salir, todos |
| `GET /tenant/invitations` | Invitaciones sin usar | Dueño y administrador |
| `POST /tenant/invitations` | Invitar un correo con un rol: devuelve el enlace | Dueño (cualquier rol), administrador (agentes) |
| `DELETE /tenant/invitations/{id}` | Anular una invitación | Igual que arriba |

La persona invitada abre el enlace (vence a las 72 horas y sirve una sola vez):

| Ruta | Qué hace |
| --- | --- |
| `POST /auth/invitations/preview` | Qué negocio la invita, con qué correo y si ya tiene cuenta |
| `POST /auth/invitations/accept` | Sin cuenta: nombre y contraseña nueva. Con cuenta: su contraseña de siempre. Queda dentro, con ese negocio elegido |

- Nadie cambia su propio rol, y el negocio siempre conserva al menos un dueño.
- El rol se lee de la base en cada petición: quitar el acceso vale al instante.
- El enlace se muestra una sola vez; la base guarda solo su huella. `PANEL_URL` del
  `.env` dice a qué dirección apunta.
- Cada cambio queda en `audit_logs` (quién, qué, cuándo y desde qué IP).
- La migración 0006 crea la tabla `tenant_invitations`, con RLS como el resto.

## Números de WhatsApp desde el panel

| Ruta | Qué hace | Quién puede |
| --- | --- | --- |
| `GET /numbers` | Los números del negocio y su estado | Todos |
| `POST /numbers` | Conectar un número nuevo: devuelve el primer QR | Dueño y administrador |
| `GET /numbers/{id}` | Pregunta a Evolution el estado real y lo guarda | Todos |
| `POST /numbers/{id}/qr` | Otro QR; con `phone`, también un código de 8 letras para vincular sin cámara | Dueño y administrador |
| `PATCH /numbers/{id}` | Encender o apagar los mensajes de grupos | Dueño y administrador |
| `POST /numbers/{id}/disconnect` | Desvincular el teléfono (se puede volver a conectar) | Dueño y administrador |
| `DELETE /numbers/{id}` | Borrar el número de Evolution (las conversaciones se conservan) | Solo el dueño |

- Hasta 5 números por negocio.
- El QR llega como imagen lista para mostrar (`data:image/png;base64,...`).
- Si Evolution no responde, la API contesta 502 y no deja nada a medias.

## Bandeja de conversaciones

| Ruta | Qué hace |
| --- | --- |
| `GET /conversations` | La bandeja, la más reciente primero. Filtros: `status`, `mode`, `mine`, `number_id`, `q` (nombre o teléfono); página siguiente con `before` |
| `GET /conversations/{id}` | Una conversación (contacto, número, asignada a, esperando respuesta) |
| `GET /conversations/{id}/messages` | Sus mensajes, el más viejo arriba; más antiguos con `before` |
| `POST /conversations/{id}/messages` | Contestar como persona (queda en la cola de salida) |
| `POST /conversations/{id}/take` | Tomarla: el bot deja de contestar y queda asignada a quien la tomó |
| `POST /conversations/{id}/release` | Devolverla al bot |
| `POST /conversations/{id}/assign` | Asignarla a alguien del negocio (dueño y administrador) |
| `POST /conversations/{id}/close` | Cerrarla; si el cliente vuelve a escribir se abre una nueva |

- Todos los roles ven la bandeja y contestan.
- Contestar en una conversación del bot la pasa a modo humano, para que el bot no
  responda encima. Cada cambio entre bot y humano queda en `handoff_events`.

## Agente y horario

| Ruta | Qué hace | Quién puede |
| --- | --- | --- |
| `GET /agent` | Las instrucciones actuales del bot | Todos |
| `PATCH /agent` | Cambiar nombre o instrucciones (crea una versión nueva) | Dueño y administrador |
| `GET /agent/default-instructions` | Las instrucciones de fábrica, para restablecer | Todos |
| `GET /agent/versions` | Historial: qué decía cada versión y quién la hizo | Todos |
| `POST /agent/versions/{n}/restore` | Volver a una versión anterior | Dueño y administrador |
| `GET /hours` | Horario de atención y si está abierto ahora | Todos |
| `PUT /hours` | Guardar el horario (`{"mon": [["08:00", "12:00"]], ...}`) y el mensaje fuera de horario | Dueño y administrador |

- El bot usa las instrucciones nuevas desde su siguiente respuesta.
- Sin horario configurado, el negocio cuenta como abierto siempre. Qué hace el bot
  fuera de horario se programa en el bloque 4.6.

## Dos usuarios de base de datos

| Usuario | Para qué | ¿Le aplica RLS? |
|---------|----------|-----------------|
| `saint_owner` | Crear tablas y correr migraciones (`DATABASE_ADMIN_URL`) | No |
| `saint_app` | La API y los workers (`DATABASE_URL`) | Sí |

La aplicación siempre abre las consultas con `tenant_session(tenant_id)`. Si se olvida,
PostgreSQL rechaza la consulta en vez de devolver datos de otro negocio.

## Claves y secretos

Las claves (Anthropic, OpenRouter, Groq, Evolution, R2) van **solo** en el archivo
`.env` del servidor. Nunca en el código, en git ni en el chat.

# Saint · Plataforma de agentes de IA para WhatsApp e Instagram

Plataforma multi-negocio (multi-tenant) de agentes de IA para atención al cliente.
El diseño completo está en el documento de la Fase 1.

## Qué hay hoy

- **WhatsApp de punta a punta:** Saint conecta números por Evolution API, recibe cada mensaje,
  lo guarda y contesta. El bot espera 15 s a que el cliente termine de escribir y contesta una
  sola vez con Claude Haiku, usando las instrucciones del negocio y los últimos 30 mensajes.
- **Panel web completo (Fase 6):** iniciar sesión, bandeja de conversaciones en tiempo real,
  números de WhatsApp con QR, instrucciones del agente con historial de versiones, horario,
  conocimiento del negocio, equipo e invitaciones, ajustes y consumo.
- **Varios negocios en una sola plataforma:** cada negocio solo ve lo suyo (PostgreSQL con RLS),
  con tres roles: dueño, administrador y agente.
- **Todavía no hay:** que el bot use el conocimiento al contestar (RAG), que respete el horario o
  pase a una persona por sí solo, transcripción de notas de voz, Instagram ni despliegue a
  producción. Ver [Lo que falta](#lo-que-falta).

Los errores y riesgos conocidos están en [RIESGOS.md](RIESGOS.md).

## Índice

- [Estado](#estado) · [Lo que falta](#lo-que-falta) · [Estructura](#estructura)
- [Puesta en marcha](#puesta-en-marcha-windows-macos-o-linux) · [Variables del `.env`](#variables-del-env) · [Pruebas](#pruebas)
- Backend: [Conectar un WhatsApp](#conectar-un-whatsapp-de-prueba-evolution-api) ·
  [Registrar un número](#registrar-el-número-de-un-negocio) · [Webhook](#recibir-los-avisos-de-evolution-webhook) ·
  [Worker](#worker-de-la-cola-ingest) · [Enviar mensajes](#enviar-mensajes-cola-de-salida) ·
  [Bot](#respuesta-del-bot-espera-de-15-s) · [Sesión](#inicio-de-sesión-del-panel) ·
  [Miembros y roles](#negocio-miembros-y-roles) · [Números](#números-de-whatsapp-desde-el-panel) ·
  [Bandeja](#bandeja-de-conversaciones) · [Agente y horario](#agente-y-horario) ·
  [Tiempo real](#bandeja-en-tiempo-real) · [Conocimiento](#conocimiento-del-negocio)
- [Panel web (Fase 6)](#panel-web-fase-6): [6.1](#inicio-de-sesión-negocio-e-inicio-61) ·
  [6.2](#piezas-comunes-del-panel-y-aceptar-una-invitación-62) · [6.3](#números-de-whatsapp-63) ·
  [6.4](#bandeja-lista-y-filtros-64) · [6.5](#bandeja-conversación-y-acciones-65) ·
  [6.6](#agente-y-horario-66) · [6.7](#conocimiento-67) · [6.8](#equipo-68) ·
  [6.9](#ajustes-y-mi-cuenta-69) · [6.10](#consumo-610)
- [Dos usuarios de base de datos](#dos-usuarios-de-base-de-datos) · [Claves y secretos](#claves-y-secretos)

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
| 4.4 | Conocimiento del negocio: fragmentos con embeddings (OpenRouter Nemotron) | Código listo (PR #12); la prueba real espera la clave de OpenRouter |
| 4.5 | RAG: que el bot use el conocimiento al contestar | Pendiente (necesita `OPENROUTER_API_KEY`) |
| 4.6 | Respetar el horario y pasar a una persona (por la IA, o si el dueño contesta desde el teléfono) | Pendiente |
| 4.7 | Notas de voz: transcripción (Groq) y archivos (R2) | Pendiente |
| 4.8 | Prueba final del bot | Pendiente |
| 5.1 | Inicio de sesión del panel: contraseñas argon2, pase JWT de 15 min y sesión de 7 días | Lista (PR #13) |
| 5.2 | Negocio, miembros y roles; invitar con enlace de un solo uso | Lista (PR #14) |
| 5.3 | Números de WhatsApp desde el panel: conectar con QR, estado, grupos, desconectar | Lista (PR #15) |
| 5.4 | Bandeja de conversaciones: leer, contestar como persona, tomar y devolver al bot | Lista (PR #16) |
| 5.5 | Instrucciones del agente con historial de versiones, y horario de atención | Lista (PR #17) |
| 5.6 | Bandeja en tiempo real: avisos al panel por `/events` (Redis + SSE) | Lista (PR #18) |
| 5.7 | Conocimiento desde el panel: subir textos, ver su estado, reintentar y borrar | Lista (PR #19) |
| 6.1 | Panel: inicio de sesión, elegir negocio, menú e Inicio en tiempo real | Lista |
| 6.2 | Panel: piezas comunes (ventanas, avisos, campos) y aceptar invitación (`/invitacion`) | Lista |
| 6.3 | Panel: Números de WhatsApp (QR, estado, grupos, desconectar, borrar) | Lista |
| 6.4 | Panel: Bandeja, lista y filtros | Lista |
| 6.5 | Panel: Bandeja, conversación y acciones (contestar, tomar, devolver, asignar, cerrar) | Lista |
| 6.6 | Panel: Agente (instrucciones y versiones) y horario | Lista |
| 6.7 | Panel: Conocimiento | Lista |
| 6.8 | Panel: Equipo (miembros, roles, invitaciones) | Lista |
| 6.9 | Panel: Ajustes del negocio y mi cuenta (cambiar contraseña, cerrar todas las sesiones) | Lista |
| 6.10 | Consumo: contadores diarios, `GET /usage` y su pantalla | Lista |
| 6.11 | Cierre del panel: totales de Inicio (`GET /conversations/counts`), revisión en el móvil, prueba completa | Lista |
| 7 | Despliegue: Dockerfiles, Caddy o nginx, integración continua y repositorio git | Pendiente |

## Lo que falta

| Qué | Por qué importa | Dónde |
|-----|-----------------|-------|
| **4.5 RAG** | El bot todavía no usa el conocimiento: contesta solo con las instrucciones y el historial | Bloque 4.5 |
| **4.6 Horario y pasar a humano** | El horario y el mensaje fuera de horario se guardan pero el bot no los usa; la IA no puede pasar una conversación a una persona; si el dueño contesta desde su teléfono, el bot sigue en modo bot | Bloque 4.6 |
| **4.7 Notas de voz** | Los audios quedan sin transcribir (`transcription_status = pending`) | Bloque 4.7 |
| **Instagram** | Solo existe en el esquema (`Channel.INSTAGRAM`); todo el código usa WhatsApp | Sin bloque aún |
| **Despliegue** | No hay Dockerfile del backend ni del panel, ni Caddy/nginx, ni integración continua. La carpeta tampoco es un repositorio git | Fase 7 |
| **Recuperar contraseña** | Hoy solo se cambia estando dentro | Sin bloque aún |

Ver el detalle de cada riesgo (con archivo y línea) en [RIESGOS.md](RIESGOS.md).

## Estructura

```
.
├── docker-compose.yml          PostgreSQL 16 + pgvector, Redis y Evolution API v2.3.7
├── docker/postgres/            Scripts que crean el usuario de la app y la base de Evolution
├── .env.example                Variables de entorno (copiar a .env)
├── RIESGOS.md                  Errores y riesgos conocidos, con prioridad
├── frontend/                   Panel web (React 18 + TypeScript + Vite)
│   ├── vite.config.ts          Servidor de desarrollo y proxy hacia la API
│   └── src/
│       ├── main.tsx, App.tsx   Arranque y rutas (según la sesión: entrar, elegir negocio, panel)
│       ├── api/                Una función por ruta de la API (client, auth, account, numbers,
│       │                       inbox, agent, knowledge, team, usage) y sus tipos (types.ts)
│       ├── auth/session.tsx    Sesión, rol (useRole) y aceptar invitación
│       ├── realtime/events.ts  Conexión a /events (SSE) con reconexión y renovación del pase
│       ├── layout/             Menú y barra superior; datos de Inicio y avisos en vivo (dashboard.tsx)
│       ├── pages/              Una por pantalla: Login, ChooseBusiness, AcceptInvitation, Home,
│       │                       Numbers, Inbox, Agent, Knowledge, Team, Settings, Usage
│       ├── components/         Piezas comunes (Button, Field, Modal, Toast...) y las tarjetas
│       │                       de cada pantalla (HoursEditor, ConversationPane, UsageChart...)
│       ├── lib/                Lógica sin pantalla: formatos, horario, permisos, estados y los
│       │                       hooks que cargan datos (useResource, useInboxList, useMessages)
│       └── __tests__/          Pruebas (Vitest + Testing Library)
└── backend/
    ├── pyproject.toml          Dependencias (se instalan con uv)
    ├── alembic.ini             Configuración de migraciones
    ├── app/
    │   ├── main.py             Crea la app FastAPI (conexiones al arrancar y apagar)
    │   ├── api/health.py       GET /health: estado de PostgreSQL y Redis
    │   ├── api/webhooks.py     POST /webhooks/evolution/{public_id}: avisos de Evolution
    │   ├── api/auth.py         /auth/...: entrar, renovar, elegir negocio, mi cuenta, contraseña, salir, invitaciones
    │   ├── api/tenant.py       /tenant/...: datos del negocio, miembros, roles e invitaciones
    │   ├── api/numbers.py      /numbers/...: conectar números con QR, estado, grupos, borrar
    │   ├── api/conversations.py  /conversations/...: bandeja, mensajes, contestar, tomar, devolver
    │   ├── api/agent.py        /agent y /hours: instrucciones del bot, versiones y horario
    │   ├── api/events.py       GET /events: avisos en tiempo real para el panel
    │   ├── api/knowledge.py    /knowledge/...: subir textos del negocio, ver estado, reintentar, borrar
    │   ├── api/usage.py        GET /usage: consumo del negocio por día (solo dueño y administrador)
    │   ├── core/config.py      Lee la configuración del .env
    │   ├── core/redis.py       Conexión a Redis
    │   ├── core/events.py      Publicar avisos al panel en el canal Redis del negocio
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
    │   ├── services/auth.py    Sesiones del panel: entrar, renovar, cerrar, contraseña, freno a intentos
    │   ├── services/members.py Qué puede hacer cada rol, miembros e invitaciones
    │   ├── services/audit.py   Registro de quién hizo qué (audit_logs)
    │   ├── services/numbers.py Números del negocio desde el panel y quién puede tocarlos
    │   ├── services/inbox.py   Bandeja: listar, leer, contestar, tomar, devolver, asignar, cerrar
    │   ├── services/agent_settings.py  Instrucciones del agente (con versiones) y horario
    │   ├── services/hours.py   Validar el horario y saber si el negocio está abierto
    │   ├── services/knowledge.py  Conocimiento del negocio: fragmentos y embeddings
    │   ├── services/knowledge_panel.py  El conocimiento desde el panel y quién puede tocarlo
    │   ├── services/usage.py   Contadores diarios del consumo (usage_daily) y su reporte
    │   ├── integrations/llm.py  Cliente de Claude (Anthropic)
    │   ├── integrations/embeddings.py  Cliente de embeddings (OpenRouter)
    │   ├── integrations/evolution/  Cliente de Evolution API (instancias, QR, estado)
    │   ├── workers/ingest.py   Worker ARQ de la cola ingest (+ barrido cada minuto)
    │   ├── scripts/evolution_qr.py  Prueba manual: conectar un WhatsApp con QR
    │   ├── scripts/registrar_numero.py  Registrar el número de un negocio en Saint
    │   ├── scripts/enviar_mensaje.py  Prueba manual: enviar un WhatsApp desde Saint
    │   ├── scripts/crear_usuario.py  Crear un usuario del panel y darle acceso a un negocio
    │   ├── scripts/cargar_conocimiento.py  Cargar, listar y borrar el conocimiento
    │   ├── db/base.py          Clase Base, convención de nombres y piezas comunes
    │   ├── db/ids.py           Generador de UUID v7
    │   ├── db/session.py       Conexión y sesiones con el negocio activo (RLS)
    │   └── models/             Un archivo por tabla (20) + enums.py
    ├── migrations/
    │   ├── env.py              Cómo se conecta Alembic
    │   └── versions/           0001 esquema inicial (tablas, índices, ENUM y RLS) · 0002 avisos
    │                           pendientes · 0003 estado de conexión · 0004 cola de salida ·
    │                           0005 respuestas pendientes · 0006 invitaciones ·
    │                           0007 negocio activo y envíos atascados
    ├── ejemplos/               Texto de ejemplo para cargar como conocimiento
    ├── logs/                   Registros de la API, el worker y el panel al correrlos en segundo plano
    └── tests/                  Pruebas de migración, RLS, webhooks, bot, panel y consumo
```

## Puesta en marcha (Windows, macOS o Linux)

Requisitos: Docker Desktop, [uv](https://docs.astral.sh/uv/) (instala Python 3.12+ solo) y
Node.js 20 o más nuevo (para el panel).

```bash
cp .env.example .env            # en Windows: copy .env.example .env
# Edita .env y cambia las contraseñas (deben coincidir en las URLs) y genera JWT_SECRET y FERNET_KEY.

docker compose up -d            # PostgreSQL, Redis y Evolution API

cd backend
uv sync                         # crea .venv e instala las dependencias
uv run alembic upgrade head     # crea las 20 tablas (migraciones 0001 a 0007)
```

Después hay que crear tu negocio y tu usuario (una sola vez): primero
[registrar el número del negocio](#registrar-el-número-de-un-negocio), que crea el negocio si
no existe, y luego [crear tu usuario](#inicio-de-sesión-del-panel) con `crear_usuario`.

Para trabajar con todo encendido hacen falta **tres procesos** (una terminal cada uno):

```bash
# 1) La API. Escucha en todas las interfaces porque Evolution la llama desde dentro de Docker.
cd backend && uv run uvicorn app.main:app --host 0.0.0.0 --port 8000

# 2) El worker: procesa los avisos, envía los mensajes, contesta con la IA y calcula embeddings.
cd backend && uv run arq app.workers.ingest.WorkerSettings

# 3) El panel web.
cd frontend && npm install && npm run dev        # http://127.0.0.1:5173
```

- Con la API corriendo, abre http://localhost:8000/health (debe decir `"status":"ok"`) y
  http://localhost:8000/docs para ver la documentación interactiva (solo en desarrollo).
- La API y el worker **no se recargan solos** (salvo que uses `--reload`): después de cambiar
  código del backend hay que reiniciarlos. El panel sí se recarga solo.
- **Windows:** si ya tienes un PostgreSQL propio ocupando el puerto 5432, pon `POSTGRES_PORT=5433`
  en el `.env` y usa ese puerto en `DATABASE_URL` y `DATABASE_ADMIN_URL`.

Comandos útiles:

```bash
uv run alembic downgrade base   # deshace todo (¡borra los datos!)
uv run alembic upgrade head --sql   # muestra el SQL sin ejecutarlo
uv run alembic check            # avisa si los modelos cambiaron y falta una migración
uv run ruff check . && uv run ruff format .   # revisión y formato del código
```

## Variables del `.env`

Copia `.env.example` a `.env`. Las claves van **solo** ahí, nunca en el código ni en git.

| Variable | Para qué | ¿Obligatoria? |
|----------|----------|---------------|
| `POSTGRES_*`, `APP_DB_*`, `EVOLUTION_DB_*` | Usuarios, contraseñas y puertos de los contenedores | Sí |
| `DATABASE_URL`, `DATABASE_ADMIN_URL`, `REDIS_URL` | Conexiones de la app, de las migraciones y de Redis | Sí |
| `APP_ENV` | `development` (tu PC: abre `/docs` y la cookie sin `Secure`) o `production`. Si falta, se asume `production`. En producción la API y el worker no arrancan con contraseñas de ejemplo ni sin `https://` | Sí |
| `EVOLUTION_BASE_URL`, `EVOLUTION_API_KEY` | Hablar con Evolution API | Sí |
| `PUBLIC_WEBHOOK_BASE_URL` | Dirección con la que Evolution llega a la API (`host.docker.internal:8000` en local) | Sí |
| `FERNET_KEY` | Cifra los tokens de los números guardados | Sí |
| `JWT_SECRET` | Firma los pases de sesión (32+ caracteres) | Sí |
| `PANEL_ORIGINS`, `PANEL_URL` | Desde dónde llama el panel y a dónde apuntan los enlaces de invitación | No (hay valores de desarrollo) |
| `ANTHROPIC_API_KEY` | La IA del bot. Sin ella, el bot contesta con un eco ("Recibí: ...") | Para contestar con IA |
| `BOT_TEST_PHONES` | Solo para pruebas: el bot contesta únicamente a estos teléfonos | No |
| `OPENROUTER_API_KEY`, `EMBEDDING_MODEL` | Embeddings del conocimiento. Sin la clave, lo que subas queda en "Falló" | Para el conocimiento |
| `LLM_INPUT_USD_PER_MILLION`, `LLM_OUTPUT_USD_PER_MILLION` | Precio por millón de tokens, para estimar el costo en Consumo. Sin ellos no se muestra costo | No |
| `GROQ_API_KEY`, `R2_*` | Reservadas para las notas de voz (bloque 4.7); todavía no se usan | No |

## Pruebas

| Dónde | Comando | Qué cubre |
|-------|---------|-----------|
| Backend | `cd backend && uv run pytest` | 265 pruebas: migraciones, RLS, webhooks, cola, bot, sesión, miembros, bandeja, agente, conocimiento, consumo. Necesita PostgreSQL y Redis; crea y borra la base `saint_test` |
| Backend | `uv run ruff check . && uv run ruff format --check .` | Lint y formato |
| Panel | `cd frontend && npm test` | 115 pruebas con la API simulada |
| Panel | `npm run typecheck` · `npm run build` | Revisión de tipos y compilación |

Las pruebas del panel usan una API simulada: no sustituyen una prueba manual con WhatsApp y la
API reales (los pasos "Para probarlo" de cada pantalla están más abajo).

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

Desde el panel, lo mismo se hace en **Números de WhatsApp** (ver [6.3](#números-de-whatsapp-63)).

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
responde enseguida; si Evolution reintenta un aviso que ya estaba, no se duplica. Después
lo toma el worker de la cola ingest (ver más abajo).

La API tiene que escuchar en todas las interfaces, porque Evolution la llama desde dentro
de Docker (`host.docker.internal:8000`): `uvicorn ... --host 0.0.0.0 --port 8000`.

Escribe al número conectado desde otro teléfono y mira la tabla `webhook_events`.

## Worker de la cola ingest

Cada aviso guardado se encola en Redis (cola `saint:ingest`). El worker lo toma, llama al
manejador de su tipo y lo marca `processed`; si falla, lo reintenta (10 s, 20 s, 40 s...)
y tras 5 intentos lo deja en `failed` con el error. Cada minuto hace un barrido por si
algún aviso no se pudo encolar (por ejemplo, Redis estaba caído). Los mensajes se guardan
en `contacts`, `conversations` y `messages`, y los avisos de conexión actualizan el
estado del número en `channel_instances` (conectado, desconectado o bloqueado).

El mismo worker envía los mensajes de la cola de salida, escribe las respuestas del bot y
calcula los embeddings del conocimiento:

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
  Si a los 10 minutos no llegó ese aviso, el barrido lo marca `failed` con un aviso
  ("puede que haya salido"); si el aviso llega después, igual pasa a `sent`.
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
  grupos, a contactos bloqueados, en negocios desactivados o borrados (su webhook
  responde 404 desde la migración 0007), ni a mensajes de hace más de una hora.
- Para probar sin molestar a clientes reales, pon en el `.env`
  `BOT_TEST_PHONES=+58 412 1234567` (varios separados por coma): solo contestará a esos.
- La migración 0005 da por atendidos los mensajes que llegaron antes del bot.
- **Todavía no** usa el conocimiento (4.5), ni el horario, ni pasa a una persona por sí solo
  (4.6), ni entiende notas de voz (4.7).

## Inicio de sesión del panel

Hace falta `JWT_SECRET` en el `.env` (genera una con
`python -c "import secrets; print(secrets.token_urlsafe(48))"`). El primer usuario se crea
desde la terminal; los demás entran por invitación desde el panel.

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
| `PATCH /auth/me` | Cambiar mi nombre |
| `POST /auth/password` | Cambiar mi contraseña (cierra las demás sesiones) |
| `POST /auth/logout` | Cierra la sesión de este navegador |
| `POST /auth/logout-all` | Cierra la sesión en todos mis dispositivos |

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
| `GET /conversations/counts` | Los contadores de Inicio y del menú sobre todas las abiertas: `open`, `waiting` (esperan respuesta), `human` (las atiende una persona), `mine` (asignadas a mí) |
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

## Bandeja en tiempo real

El panel abre `GET /events` (con `Authorization: Bearer <pase>`) y la deja abierta.
Cuando algo cambia en el negocio, la API o el worker publican un aviso en Redis
(`saint:events:<negocio>`) y llega al panel así:

```
event: conversation.changed
data: {"type": "conversation.changed", "conversation_id": "..."}
```

| Aviso | Cuándo |
| --- | --- |
| `ready` | Al abrir (o reabrir) la conexión: el panel recarga lo que pudo cambiar mientras no la tenía |
| `conversation.changed` | Mensaje nuevo del cliente, respuesta del bot o de una persona, tomar, devolver, asignar, cerrar |
| `inbox.changed` | Otro cambio en la bandeja, por ejemplo un mensaje que pasó a enviado o entregado |
| `numbers.changed` | Un número se conectó, desconectó, cambió o se borró |
| `knowledge.changed` | Un documento de conocimiento quedó listo, falló o se borró |
| `session.expired` | Venció el pase o la persona perdió el acceso: el panel renueva y se reconecta |

- El aviso lleva solo el tipo y un id, nunca el texto. El panel vuelve a pedir los datos
  a la API, que sigue decidiendo qué puede ver cada persona.
- Cada 15 s llega una línea `: ping` para que la conexión no se corte.
- Si Redis falla, la acción igual se guarda; el panel lo verá al recargar.
- Detrás de Caddy o nginx, la respuesta lleva `X-Accel-Buffering: no` para que no se guarde.
- El horario, el agente, el equipo y el consumo **no** tienen aviso propio: el panel los
  recarga al reconectar o cuando la pantalla que los cambia lo pide.

Probarlo a mano: `curl -N -H "Authorization: Bearer <pase>" http://localhost:8000/events`

## Conocimiento del negocio

Lo que el bot sabrá del negocio (horarios, productos, precios...) se carga desde un
archivo de texto (.txt o .md). Se parte en fragmentos de unos 500 tokens y a cada uno se
le calcula su embedding con OpenRouter (modelo `nvidia/nemotron-3.5-embed-8b:free`,
recortado a 1024 números). Hace falta `OPENROUTER_API_KEY` en el `.env`.

```bash
cd backend
uv run python -m app.scripts.cargar_conocimiento cargar panaderia-prueba ejemplos/panaderia_prueba.md
uv run python -m app.scripts.cargar_conocimiento listar panaderia-prueba
uv run python -m app.scripts.cargar_conocimiento borrar panaderia-prueba panaderia_prueba
```

- El título es el nombre del archivo (o `--titulo`). Cargar otro texto con el mismo
  título reemplaza al anterior; cargar el mismo texto otra vez no hace nada.
- El modelo gratuito guarda lo que se le manda: carga solo información pública.
- Todavía el bot no usa este conocimiento al responder: eso llega en el bloque 4.5.

### Desde el panel

| Ruta | Qué hace | Quién puede |
| --- | --- | --- |
| `GET /knowledge` | Los documentos y su estado | Todos |
| `GET /knowledge/{id}` | Un documento con sus fragmentos | Todos |
| `POST /knowledge` | Subir un texto (`{"title": ..., "text": ...}`, hasta 200 000 caracteres) | Dueño y administrador |
| `POST /knowledge/{id}/retry` | Reintentar uno que falló | Dueño y administrador |
| `DELETE /knowledge/{id}` | Borrarlo con sus fragmentos | Dueño y administrador |

- Subir responde enseguida (202) con el documento en `processing`. El worker pide los
  embeddings y lo deja `ready` o `failed`; al panel le llega `knowledge.changed` por
  `/events`.
- Sin `OPENROUTER_API_KEY`, queda `failed` con ese aviso. El texto queda guardado: al
  poner la clave y reiniciar el worker, basta con reintentar.
- Un texto nuevo con el mismo título reemplaza al viejo solo cuando el nuevo queda listo.

## Panel web (Fase 6)

El panel está en `frontend/` (React + TypeScript con Vite). Necesita Node.js 20 o más
nuevo y la API encendida en `http://127.0.0.1:8000`.

```bash
cd frontend
npm install        # la primera vez
npm run dev        # abre http://127.0.0.1:5173
```

- En desarrollo, Vite pasa a la API todo lo que empieza por `/auth`, `/tenant`,
  `/numbers`, `/conversations`, `/agent`, `/hours`, `/knowledge`, `/usage`, `/events` y `/health`.
  Así el panel y la API se ven como una sola dirección y la cookie de sesión funciona
  sin configurar nada más. Otra dirección de la API: `SAINT_API_URL=http://... npm run dev`.
- El pase de 15 minutos vive solo en la memoria de la página y se renueva solo. Al
  recargar, el panel pide uno nuevo con la cookie y recuerda el negocio elegido.
- Una sola conexión a `/events` para todo el panel: las pantallas escuchan los avisos sin abrir otra.
- Pruebas: `npm test`. Revisión de tipos: `npm run typecheck`. Compilar: `npm run build`.
- En el teléfono, el menú pasa a una barra de abajo y la bandeja muestra la lista o la conversación.

| Pantalla | Dirección | Bloque |
| --- | --- | --- |
| Iniciar sesión | `/iniciar-sesion` | 6.1 |
| Elegir negocio (si tienes varios) | `/elegir-negocio` | 6.1 |
| Inicio | `/` | 6.1 |
| Aceptar una invitación (con o sin sesión) | `/invitacion#<token>` | 6.2 |
| Números de WhatsApp | `/numeros` | 6.3 |
| Bandeja (lista, filtros y conversación) | `/bandeja` y `/bandeja/<id>` | 6.4 y 6.5 |
| Agente y horario | `/agente` | 6.6 |
| Conocimiento | `/conocimiento` | 6.7 |
| Equipo | `/equipo` | 6.8 |
| Ajustes (negocio y mi cuenta) | `/ajustes` | 6.9 |
| Consumo | `/consumo` | 6.10 |

**Quién puede qué** (resumen; la API siempre vuelve a comprobarlo y el detalle está en cada pantalla):

| | Dueño | Administrador | Agente |
|---|---|---|---|
| Ver bandeja, números, agente, conocimiento y equipo | Sí | Sí | Sí |
| Contestar, tomar y devolver conversaciones | Sí | Sí | Sí |
| Asignar conversaciones | Sí | Sí | No |
| Conectar números, cambiar agente, horario, conocimiento y datos del negocio | Sí | Sí | No |
| Borrar un número | Sí | No | No |
| Invitar, cambiar roles, quitar gente | A todos | Solo agentes | No |
| Ver el consumo | Sí | Sí | No |

### Inicio de sesión, negocio e Inicio (6.1)

- **Iniciar sesión:** correo y contraseña, y "Mantener la sesión iniciada" (sin marcar, la cookie
  se borra al cerrar el navegador). Un correo o contraseña malos y los demasiados intentos (429,
  esperar 15 minutos) se explican con un mensaje claro. Todavía no hay "recuperar contraseña":
  quien la olvida debe pedir una invitación nueva.
- **Elegir negocio:** aparece si la persona tiene varios; con uno solo, entra directo. Se puede
  cambiar de negocio en cualquier momento desde la barra de arriba.
- **Inicio:** cuatro contadores (abiertas, esperan respuesta, las atiende una persona, asignadas
  a mí; desde el 6.11 cuentan todas las abiertas con `GET /conversations/counts`), "Primeros pasos" (conectar el número, revisar el agente,
  subir conocimiento, invitar al equipo; cada uno con su enlace) y la tarjeta "Tu número". Se
  actualiza solo con los avisos de `/events`.
- **Barra superior:** "Agente IA activo / sin número" y "Abierto ahora / Fuera de horario".

### Cierre del panel (6.11)

- **Contadores sin tope:** Inicio y el número del menú (esperan respuesta) salen de
  `GET /conversations/counts`, que cuenta en la base todas las abiertas. Antes se pedían las
  primeras 100 conversaciones y con más se mostraba "100+".
- **Revisión en el móvil:** todas las pantallas revisadas a 375 px y 320 px de ancho, sin
  desplazamiento horizontal. En Consumo, las opciones de periodo y de gráfico se deslizan de
  lado cuando no caben.
- **Prueba completa:** iniciar sesión, ver Inicio, abrir una conversación de la bandeja,
  contestar como persona y volver a Inicio: los contadores cambian solos (una más atendida por
  una persona y asignada a mí, una menos esperando respuesta).

### Piezas comunes del panel y aceptar una invitación (6.2)

El enlace que devuelve `POST /tenant/invitations` abre `/invitacion#<token>`. El token va
después del `#` para que no llegue al servidor en la dirección; el panel lo lee una vez y
lo quita de la barra.

- Sin cuenta en Saint: pide nombre y contraseña nueva (dos veces, mínimo 10 caracteres).
- Con cuenta: pide su contraseña de siempre.
- Vencida (72 h), usada o anulada: lo explica y pide un enlace nuevo.
- Al aceptar queda dentro con ese negocio elegido. Si había otra sesión abierta, avisa que
  se cambia de cuenta.

Para probarlo: con la API en marcha, entra a `http://127.0.0.1:8000/docs`, autorízate con
el pase de un dueño y llama a `POST /tenant/invitations` con un correo nuevo (o hazlo desde
**Equipo**). Abre el `link` que devuelve en una ventana privada.

Piezas que usan todas las pantallas:

| Pieza | Archivo | Para qué |
| --- | --- | --- |
| Funciones de la API | `src/api/*.ts` | Una función por ruta del backend, con sus tipos (`src/api/types.ts`) |
| `Button`, `TextField`, `TextArea`, `SelectField`, `Switch` | `src/components/Button.tsx`, `Field.tsx` | Botones con estado "cargando"; campos con ayuda, error y contador |
| `Modal`, `ConfirmDialog` | `src/components/Modal.tsx`, `ConfirmDialog.tsx` | Ventanas; confirmar antes de borrar (con texto a escribir si no se deshace) |
| `useToast()` | `src/components/Toast.tsx` | Avisos cortos: "Guardado", "No se pudo enviar" |
| `EmptyState`, `Spinner`, `StatusPill` | `src/components/Feedback.tsx` | Listas vacías, carga y etiquetas de estado |
| `useResource()` | `src/lib/useResource.ts` | Cargar datos de una pantalla y recargarlos |
| `useEvents()`, `useDashboardRefresh()` | `src/layout/dashboard.tsx` | Escuchar `/events` desde una pantalla (sin abrir otra conexión) y pedir que Inicio se actualice |
| `useRole()` | `src/auth/session.tsx` | Mostrar u ocultar botones según el rol (la API lo vuelve a comprobar) |
| Formatos y estados | `src/lib/format.ts`, `src/lib/status.ts` | "hace 5 min", teléfonos, números con punto de miles, roles y el nombre y color de cada estado |

### Números de WhatsApp (6.3)

| Quién | Qué puede hacer |
| --- | --- |
| Todos | Ver los números y su estado; "Revisar estado" (pregunta a WhatsApp) |
| Dueño y administrador | Conectar un número, mostrar otro QR, prender o apagar los grupos, desconectar |
| Solo el dueño | Borrar (hay que escribir el número para confirmar) |

- **Conectar:** elige si recibe grupos y genera el QR. El QR se renueva solo cada 25 s y,
  mientras la ventana está abierta, se pregunta el estado cada 4 s y con cada aviso
  `numbers.changed`. Al escanear, la ventana se cierra sola y avisa "Número conectado".
- **Sin cámara:** la pestaña "Usar un código" pide el teléfono con código de país y muestra
  un código de 8 letras para escribirlo en WhatsApp > Dispositivos vinculados.
- **Límite:** 5 números por negocio. Con el límite, el botón se apaga y lo explica.
- Si el teléfono se desconecta o WhatsApp bloquea el número, la tarjeta lo dice.
- Si Evolution no responde (error 502), se ve el aviso y no queda nada a medias.

Para probarlo: `docker compose up -d`, API y worker en marcha, entra a `/numeros` como dueño
y pulsa "Conectar un número". Escanea el QR desde un WhatsApp de prueba.

### Bandeja: lista y filtros (6.4)

Lista a la izquierda y la conversación elegida a la derecha (en el teléfono se ve una u
otra). Cada fila muestra el nombre o teléfono, el último mensaje ("Tú:" si fue una
respuesta), cuánto hace, quién la atiende (agente IA o persona), "Espera respuesta" y a
quién está asignada.

- **Filtros** (se guardan en la dirección, así se pueden compartir y el botón atrás
  funciona): Abiertas / Cerradas, quién atiende (agente IA o persona), "solo las asignadas a
  mí", el número (si hay más de uno) y la búsqueda por nombre o teléfono, que espera a que
  termines de escribir.
- **Ver más antiguas** pide la página siguiente con `before` (de 30 en 30).
- **Tiempo real:** con cada aviso `conversation.changed` o `inbox.changed` se vuelve a
  pedir la primera página y se mezcla con lo ya abierto, sin perder las páginas de abajo.
- La conversación elegida se abre a la derecha (ver abajo).

### Bandeja: conversación y acciones (6.5)

Arriba, quién es la persona, su teléfono, el número por el que escribe, quién la atiende y
los botones. En medio, los mensajes del más viejo al más nuevo, agrupados por día. Abajo,
la caja para contestar.

| Acción | Quién | Qué pasa |
| --- | --- | --- |
| Contestar | Todos | Enter envía, Shift + Enter hace salto de línea (hasta 4096 caracteres). Si atendía el agente IA, se aparta y la conversación queda asignada a quien contestó |
| Tomar | Todos | El agente IA deja de contestar; queda asignada a quien la tomó |
| Devolver al agente | Todos | Vuelve al agente IA y queda sin asignar |
| Asignar | Dueño y administrador | A alguien del equipo, o a nadie |
| Cerrar | Todos | Sale de las abiertas; si el cliente vuelve a escribir, se abre una nueva |

- **Burbujas:** se distinguen el cliente, el agente IA, una persona desde el panel (con su
  nombre) y el dueño desde el teléfono. Lo enviado muestra su estado: En cola, Enviado,
  Entregado, Leído o No se envió (con el motivo).
- **Audios y archivos:** se ve una etiqueta (Nota de voz, Foto, Documento...). Las notas de
  voz muestran su transcripción cuando exista (bloque 4.7); el archivo en sí se ve en el
  WhatsApp del negocio.
- **Leyendo lo de arriba:** si llega un mensaje nuevo, no te mueve; aparece "Mensajes
  nuevos ↓". "Ver mensajes anteriores" carga de 50 en 50 y la pantalla se queda en su sitio.
- **Tiempo real:** los avisos de `/events` actualizan los mensajes, el estado de lo enviado
  y el modo de la conversación, sin recargar.
- Una conversación cerrada solo se puede leer.

### Agente y horario (6.6)

Tres tarjetas en `/agente`: las instrucciones, el historial de versiones y el horario. Todos
las ven; solo el dueño y los administradores las cambian (a los demás les salen en solo
lectura).

- **Instrucciones:** nombre (hasta 80 caracteres) e instrucciones (de 20 a 8000). Cada cambio
  de instrucciones crea una versión nueva y el agente la usa desde su siguiente respuesta;
  cambiar solo el nombre no crea versión. "Restablecer las de fábrica" trae el texto original
  al cuadro sin guardarlo (hay que pulsar Guardar). Si cierras la pestaña con cambios sin
  guardar, el navegador pregunta.
- **Historial:** quién escribió cada versión y cuándo; "Ver" muestra el texto completo y
  "Volver a esta" lo restaura **como una versión nueva** (no se pierde ninguna).
- **Horario:** hasta 3 tramos por día (ej. 08:00–12:00 y 14:00–18:00), un día sin tramos está
  cerrado, "Copiar a todos los días" repite un día en toda la semana. Las horas son de la zona
  horaria del negocio. Se avisa antes de guardar si el cierre no es posterior a la apertura o
  si dos tramos se pisan.
  - Con el interruptor "Tengo un horario de atención" apagado, el negocio cuenta como
    **abierto siempre**. Encendido con todos los días cerrados, figura como **cerrado
    siempre** (el panel lo advierte).
  - El campo de hora no admite `24:00`; un cierre guardado a medianoche se muestra como 23:59.
- **Mensaje fuera de horario** (hasta 500 caracteres): se guarda, pero **el agente todavía
  no lo usa**: contestar distinto fuera de horario llega con el bloque 4.6.

### Conocimiento (6.7)

Lista de los documentos con su estado, tamaño, quién los subió y cuándo. Todos los ven; solo
el dueño y los administradores suben, reintentan y borran.

- **Subir:** pegar un texto o cargar un `.txt` / `.md` (se lee en el navegador; hasta 1 MB y
  200 000 caracteres). El título se sugiere a partir del nombre del archivo. Un título repetido
  reemplaza al documento anterior cuando el nuevo queda listo; el mismo texto otra vez no hace
  nada (el panel lo avisa).
- **Estados:** Procesando (calculando los embeddings), Listo y Falló (con el motivo; el texto
  no se pierde). Los avisos `knowledge.changed` de `/events` actualizan la lista y, si
  `/events` no está disponible, se vuelve a preguntar cada 5 s mientras algo se procesa.
- **Reintentar:** disponible para los que fallaron y para los que llevan más de 5 minutos
  procesándose (por si no se pudo encolar). Sin `OPENROUTER_API_KEY` en el `.env` todo queda
  en "Falló"; al poner la clave y reiniciar el worker, basta con reintentar.
- **Ver fragmentos:** el texto partido en trozos de unos 500 tokens, con cuáles ya están
  listos para buscar.
- **Borrar:** pide confirmación y no se puede deshacer.
- **Ojo:** el servicio gratuito de embeddings guarda lo que recibe. Sube solo información
  pública del negocio (horarios, productos, precios), nunca datos de clientes ni claves. Y el
  agente **todavía no usa** este conocimiento al contestar: llega con el bloque 4.5.

### Equipo (6.8)

Tres tarjetas en `/equipo`: los miembros, las invitaciones pendientes y una tabla de qué puede
hacer cada rol. Todos ven a los miembros; las invitaciones solo las ven el dueño y los
administradores.

| Acción | Dueño | Administrador | Agente |
| --- | --- | --- | --- |
| Invitar | Con cualquier rol | Solo agentes | No |
| Cambiar el rol de alguien | Sí | No (solo gestiona agentes) | No |
| Quitar a alguien | A todos | Solo agentes | No |
| Ver y anular invitaciones | Todas | Las de agentes | No |
| Salir del negocio | Sí, si queda otro dueño | Sí | Sí |

- **Invitar:** correo y rol. Saint **no manda correos**: la API devuelve un enlace de un solo
  uso (vence en 72 horas) que se muestra **una sola vez**, con botón "Copiar", para que lo
  compartas por WhatsApp o correo. Si se pierde, se vuelve a invitar al mismo correo: el enlace
  anterior deja de servir. Se abre en `/invitacion#<token>` (bloque 6.2).
- **Roles:** dar o quitar el rol de dueño pide confirmación. Nadie cambia su propio rol, y el
  negocio siempre conserva al menos un dueño: el único dueño no puede salir hasta nombrar a otro.
- **Quitar:** pide confirmación y la persona deja de entrar en ese mismo momento (el rol se lee
  de la base en cada petición).
- **Salir del negocio:** renueva la sesión y te lleva a tu otro negocio, o a elegir uno si
  tienes varios.
- Cada cambio queda en `audit_logs` (quién, qué, cuándo y desde qué IP).

### Ajustes y mi cuenta (6.9)

Cuatro tarjetas en `/ajustes`:

- **Negocio** (dueño y administrador; los demás lo ven en solo lectura): nombre, país y zona
  horaria (`PATCH /tenant`). La zona horaria es la que usa el horario de atención y el consumo. El
  identificador (slug) se muestra pero no cambia: lo usan los scripts.
- **Mi cuenta:** mi nombre (`PATCH /auth/me`), como me ve mi equipo. El correo no se cambia
  desde el panel.
- **Cambiar mi contraseña** (`POST /auth/password`): pide la actual y la nueva dos veces (mínimo
  10 caracteres, distinta de la actual). **Cierra las demás sesiones** (otros navegadores y
  teléfonos); la de este navegador sigue. Equivocarse con la actual responde 422 (no 401, para
  que el panel no lo confunda con un pase vencido) y cuenta para el mismo freno que el inicio de
  sesión: 5 fallos bloquean 15 minutos.
- **Sesiones abiertas:** "Cerrar sesión en todos los dispositivos" (`POST /auth/logout-all`),
  incluido este. Sirve si se perdió un teléfono o se entró desde un equipo ajeno.

Las sesiones se cierran **venciéndolas**, no marcándolas como revocadas: un pase revocado que
vuelve a aparecer se toma por robado y cierra todas las sesiones de la persona (ver
`services/auth.refresh`). Así, cuando el otro dispositivo intente renovar solo recibe "sesión
terminada" y no arrastra la sesión que acaba de cambiar la contraseña.

### Consumo (6.10)

`GET /usage?period=this_month|last_month|last_7_days|last_30_days` y la pantalla `/consumo`.
Solo el dueño y los administradores lo ven (a un agente la API le responde 403 y el panel ni
se lo pide).

**Qué se cuenta** (tabla `usage_daily`, una fila por negocio y día, sumada con
`INSERT ... ON CONFLICT DO UPDATE`):

| Contador | Cuándo suma |
| --- | --- |
| Conversaciones nuevas | Al abrirse una conversación nueva |
| Mensajes recibidos | Cada mensaje nuevo de un cliente (uno repetido no cuenta dos veces) |
| Mensajes enviados | Cuando WhatsApp acepta un mensaje de Saint (bot o panel), o cuando el dueño escribe desde su teléfono. Uno que falló, o que sigue en cola, no cuenta |
| Tokens de IA (entrada y salida) | Cada llamada a la IA, **también las descartadas**: se pagan igual |
| Respuestas de IA | Sale de `llm_runs` (las de estado `ok`), no de `usage_daily` |

- **Los días son del negocio:** se cuentan en la zona horaria del negocio (Ajustes). Un
  mensaje de las 23:00 en Caracas cae en ese día aunque en UTC ya sea el siguiente.
- **Solo cuenta desde que se instaló este bloque:** el pasado no se rellena. Si el periodo
  empieza antes del primer día con datos, la pantalla lo avisa.
- **Contar nunca rompe nada:** la suma va en un savepoint; si falla, se registra el error y el
  mensaje o la respuesta siguen su camino.
- **Costo estimado:** solo aparece si pones los precios en el `.env`
  (`LLM_INPUT_USD_PER_MILLION` y `LLM_OUTPUT_USD_PER_MILLION`, dólares por millón de tokens).
  Saint no trae tarifas propias; sin precios muestra los tokens y "Sin precios".
- **Pantalla:** tarjetas con el resumen, gráfico de barras por día (mensajes, respuestas de IA,
  tokens o conversaciones; hecho con CSS) y una tabla con los números exactos de cada día.
- **Pendiente:** `audio_seconds` existe pero todavía no se llena (llega con las notas de voz, 4.7).

Con la API y el worker ya en marcha hay que **reiniciar los dos** para que empiecen a contar.

## Dos usuarios de base de datos

| Usuario | Para qué | ¿Le aplica RLS? |
|---------|----------|-----------------|
| `saint_owner` | Crear tablas y correr migraciones (`DATABASE_ADMIN_URL`) | No |
| `saint_app` | La API y los workers (`DATABASE_URL`) | Sí |

La aplicación siempre abre las consultas con `tenant_session(tenant_id)`. Si se olvida,
PostgreSQL rechaza la consulta en vez de devolver datos de otro negocio. 18 de las 20 tablas
tienen RLS; solo `users` y `refresh_tokens` no, a propósito (hay que encontrar a la persona
antes de saber su negocio).

## Claves y secretos

Las claves (Anthropic, OpenRouter, Groq, Evolution, R2) van **solo** en el archivo
`.env` del servidor. Nunca en el código, en git ni en el chat.

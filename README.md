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
| 4.1 | Enviar mensajes: cola de salida, reintentos y confirmación | Esta rama |

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
    │   ├── core/config.py      Lee la configuración del .env
    │   ├── core/redis.py       Conexión a Redis
    │   ├── core/queue.py       Cola ingest (ARQ): nombre y cómo encolar
    │   ├── core/security.py    Cifrado (Fernet) y huellas de secretos
    │   ├── services/channel_registration.py  Registrar un número y su estado
    │   ├── services/webhook_ingest.py  Validar el secreto y guardar cada aviso
    │   ├── services/webhook_processing.py  Procesar avisos: manejadores y reintentos
    │   ├── services/message_ingest.py  Aviso de mensaje -> contacto, conversación, mensaje
    │   ├── services/connection_events.py  Aviso de conexión -> estado del número
    │   ├── services/outbound.py  Cola de salida: guardar y enviar mensajes por Evolution
    │   ├── workers/ingest.py   Worker ARQ de la cola ingest (+ barrido cada minuto)
    │   ├── integrations/evolution/  Cliente de Evolution API (instancias, QR, estado)
    │   ├── scripts/evolution_qr.py  Prueba manual: conectar un WhatsApp con QR
    │   ├── scripts/registrar_numero.py  Registrar el número de un negocio en Saint
    │   ├── scripts/enviar_mensaje.py  Prueba manual: enviar un WhatsApp desde Saint
    │   ├── db/base.py          Clase Base, convención de nombres y piezas comunes
    │   ├── db/ids.py           Generador de UUID v7
    │   ├── db/session.py       Conexión y sesiones con el negocio activo (RLS)
    │   └── models/             Un archivo por tabla (19) + enums.py
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

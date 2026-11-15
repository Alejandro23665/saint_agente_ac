# Saint · Plataforma de agentes de IA para WhatsApp e Instagram

Plataforma multi-negocio (multi-tenant) de agentes de IA para atención al cliente.
El diseño completo está en el documento de la Fase 1.

## Estado

| Fase | Contenido | Estado |
|------|-----------|--------|
| 1 | Diseño (arquitectura, esquema, riesgos) | Lista |
| 2 | Modelos SQLAlchemy, migración Alembic, RLS y pruebas | Lista (PR #1) |
| 3.1 | App FastAPI mínima con `/health` | Lista (PR #2) |
| 3.2 | Evolution API en Docker y cliente en Python | Esta rama |
| 3.3–3.7 | Registrar número, webhook, cola ingest, guardado de mensajes | Pendiente |

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
    │   ├── core/config.py      Lee la configuración del .env
    │   ├── core/redis.py       Conexión a Redis
    │   ├── integrations/evolution/  Cliente de Evolution API (instancias, QR, estado)
    │   ├── scripts/evolution_qr.py  Prueba manual: conectar un WhatsApp con QR
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

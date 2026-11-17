# Saint · Errores y riesgos conocidos

Lista de problemas encontrados al revisar el código (noviembre 2026). Salen de leer el
código, no de correr pruebas. Al resolver uno, márcalo con `[x]` y anota el PR.

Prioridad: **Alta** (puede afectar a clientes reales o a la seguridad), **Media**
(falla en casos concretos), **Baja** (orden y mantenimiento).

## Errores

- [x] **Alta · Negocios desactivados siguen funcionando.** Resuelto (17/11, migración
  0007): `resolve_channel_instance` ahora también exige `tenants.is_active` y
  `tenants.deleted_at` vacío, así que el webhook de un negocio desactivado o borrado
  responde 404 y no se guarda nada. Además el bot no contesta lo que ya estaba pendiente
  cuando se desactivó (`_bot_answers` en `bot_reply.py`), ni envía una respuesta que la IA
  estaba escribiendo en ese momento. Pruebas: `test_negocio_desactivado_o_borrado` y
  `test_negocio_desactivado_no_contesta`.
  Antes: la función solo revisaba `channel_instances.deleted_at`.
- [x] **Alta · Los contactos bloqueados reciben respuesta del bot.** Resuelto (17/11):
  `_bot_answers` (`bot_reply.py`) mira `contacts.blocked`; sus mensajes se guardan y se
  ven en la bandeja, pero el bot no les contesta (tampoco si los bloquean mientras la IA
  escribe). Prueba: `test_contacto_bloqueado_no_recibe_respuesta`.
- [x] **Alta · Enlace de invitación sin pantalla.** Resuelto en el bloque 6.2: página
  `/invitacion` (`frontend/src/pages/AcceptInvitation.tsx`), que no pierde el token.
  Antes: el backend armaba
  `{PANEL_URL}/invitacion#token` (`backend/app/services/members.py:147`), pero el panel no
  tenía esa ruta. Sin sesión, redirigía a iniciar sesión y perdía el token; con sesión,
  redirigía a `/`.
- [x] **Media · Mensajes que se quedan en `sending` para siempre.** Resuelto (17/11,
  migración 0007): el barrido de cada minuto busca los que llevan más de 10 minutos en
  `sending` (`stuck_sending_messages`) y los marca `failed` con el aviso "Evolution no
  confirmó el envío... puede que haya salido" (`outbound.fail_stuck_sending`). No se
  reenvían solos para no mandarlos dos veces. Si después llega el aviso `send.message`,
  el mensaje pasa a `sent` sin duplicarse. Pruebas: `test_atascado_en_sending_queda_fallido`,
  `test_confirmacion_tardia_lo_marca_enviado` y `test_barrido_del_worker_marca_atascados`.
  Antes: `queued_outbound_messages` (0004) solo buscaba `queued`.
- [ ] **Media · Un 429 de Evolution se trata como error permanente.** `outbound.py:178`
  marca `failed` ante cualquier 4xx; el 429 (demasiadas peticiones) debería reintentarse.
- [ ] **Media · La respuesta del dueño desde su teléfono no pasa la conversación a
  humano.** Se guarda como `HUMAN_PHONE` (`message_ingest.py:250-251`) pero el modo sigue
  en `bot`: el bot contesta el siguiente mensaje del cliente encima del dueño.
- [ ] **Media · Ajustes guardados que no se aplican.**
  - `channel_instances.debounce_seconds`: el bot usa siempre los 15 s fijos
    (`bot_reply.py:46`, `workers/ingest.py:92`).
  - `agents.temperature`, `agents.tools`, `agents.handoff_rules`: se guardan y versionan,
    pero `integrations/llm.py:71-79` no se los pasa a Claude.
- [ ] **Media · Conocimiento con el mismo título.** `embed_source`
  (`knowledge.py:261-265`) borra los otros documentos con el mismo título aunque sigan en
  `processing`. Además, `retry` sobre uno que sigue en `processing`
  (`knowledge_panel.py:185`) puede lanzar dos trabajos a la vez.
- [ ] **Media · Documentos que se quedan en `processing`.** Si encolar falla
  (`api/knowledge.py:112-116`), no hay barrido que lo recupere.
- [ ] **Baja · `contacts.updated_at` no se actualiza** en el upsert de
  `message_ingest.py:186-193` (`on_conflict_do_update` no aplica `onupdate`).
- [ ] **Baja · Mismo texto con otro título conserva el título viejo**
  (`knowledge.py:183-187`).
- [ ] **Baja · `PATCH /tenant` acepta `country_code` de hasta 5 caracteres**
  (`api/tenant.py:51`) aunque la columna es `CHAR(2)` (el servicio sí lo valida).
- [ ] **Baja · Búsqueda de la bandeja sin escapar `%` y `_`** (`inbox.py:164`). No es
  inyección SQL, solo comodines inesperados.

## Riesgos de seguridad y de producción

- [x] **Alta · Valores por defecto peligrosos.** Resuelto (17/11): si falta `APP_ENV`
  se asume `production` (sin `/docs` y con la cookie `Secure`), y solo acepta
  `development` o `production`. La API y el worker revisan la configuración al arrancar
  (`Settings.check_startup` en `core/config.py`) y no arrancan, con un mensaje claro, si
  falta `JWT_SECRET` (o es corta) o `FERNET_KEY` (o no es válida). En producción además
  rechazan las contraseñas de ejemplo de la base, que falte `EVOLUTION_API_KEY` y
  `PANEL_URL` o `PANEL_ORIGINS` sin `https://`. Pruebas en `test_health.py`.
  Antes: `APP_ENV` valía `development` por defecto y las claves se validaban al usarlas
  (error 500).
- [ ] **Alta · Eco a clientes reales.** Sin `ANTHROPIC_API_KEY` el bot responde
  "Recibí: ..." (`bot_reply.py:122-127`). Si además `BOT_TEST_PHONES` está vacío, le
  llega a cualquier cliente.
- [ ] **Alta · IP del cliente detrás de un proxy.** `client_ip` usa `request.client.host`
  (`api/auth.py:104`). Detrás de Caddy todos comparten la IP del proxy: 20 contraseñas
  malas de cualquiera bloquean el acceso de todos y la auditoría guarda IPs incorrectas.
  Hay que arrancar uvicorn con `--proxy-headers --forwarded-allow-ips`.
- [ ] **Media · Los barridos dependen de un superusuario.** Las funciones
  `SECURITY DEFINER` de 0002, 0004, 0005 y 0006 funcionan porque `saint_owner` es
  superusuario en Docker. En una base administrada (sin `BYPASSRLS`), el RLS forzado haría
  fallar los barridos.
- [ ] **Media · Freno a intentos solo en login y aceptar invitación.** No hay límite en
  `/auth/refresh`, `/auth/invitations/preview`, el webhook, contestar mensajes, subir
  conocimiento ni en las conexiones a `/events`.
- [ ] **Media · Tamaño del webhook.** `await request.body()` lee todo antes de comprobar
  el límite de 1 MB (`api/webhooks.py:50-52`).
- [ ] **Media · Filas bloqueadas durante llamadas a Evolution.** `services/numbers.py`
  mantiene `FOR UPDATE` mientras llama a Evolution (hasta 20 s) en refrescar, QR, grupos,
  desconectar y borrar.
- [ ] **Media · Conexiones SSE sin límite.** Cada `/events` abre su propia conexión a
  Redis y consulta la base cada 15 s (`api/events.py:58,75`).
- [ ] **Media · Búsqueda por vectores entre negocios.** Los índices HNSW de
  `knowledge_chunks` y `catalog_items` cubren a todos los negocios. Al hacer RAG (4.5),
  filtrar por RLS después del índice puede devolver pocos resultados por negocio.
- [ ] **Media · El modelo gratuito de embeddings guarda lo que se le manda**
  (`integrations/embeddings.py:11-12`): solo cargar información pública.
- [ ] **Baja · El webhook distingue `public_id` inexistente (404) de secreto malo
  (401)** (`api/webhooks.py:64-69`). Riesgo bajo: los ids son aleatorios.
- [ ] **Baja · Sin protección contra instrucciones maliciosas del cliente** (prompt
  injection) en las instrucciones del bot.

## Datos que crecen sin límite

- [ ] `webhook_events` (los comentarios dicen 30 días), `llm_runs` (90 días) y
  `refresh_tokens` vencidos no se borran nunca. El único cron es `requeue_stuck_events`.
- [ ] Los avisos `qrcode.updated` y `connection.update` no tienen id externo y no se
  deduplican (`webhook_ingest.py:74,86`).
- [ ] La migración 0005 marca mensajes como atendidos y su `downgrade` no lo deshace.

## Orden y mantenimiento

- [ ] **La carpeta no es un repositorio git.** El código ya está en GitHub (rama
  `claude/project-thread-4ajxho`, 16/11), pero la carpeta del PC sigue sin git: los cambios
  se copian a mano. Falta fusionar los PRs #11 a #20 y esta rama en `main`.
- [ ] `backend/logs/` (unos 7 MB, con `public_id` de webhooks) no está en `.gitignore`.
- [ ] La última corrida del worker (`backend/logs/worker_5_7.log`) terminó perdiendo la
  conexión con Redis ("Acceso denegado").
- [ ] El `.env` no tiene `OPENROUTER_API_KEY`: todo el conocimiento quedará `failed`.
- [ ] `docker-compose.override.yml.bak` no está activo (por el `.bak`); el puerto 5433 se
  toma de `POSTGRES_PORT` en `.env`.
- [ ] README: dice "crea las 19 tablas" (línea 110) pero son 20, y en la estructura solo
  menciona la migración 0001 (hay siete). `migrations/env.py:11` también dice 19.
- [ ] El modelo `claude-haiku-5-5` está escrito en dos sitios (`models/agent.py:27` y
  `services/agents.py:24`).
- [ ] `app_role()` está copiada en las migraciones 0002, 0004, 0005 y 0006, y las
  migraciones importan `app.core.config`.
- [x] Panel: Inicio contaba solo las primeras 100 conversaciones. Resuelto en el 6.11
  (17/11) con `GET /conversations/counts`.
- [ ] Panel: horario, agente y miembros no tienen aviso propio en
  `/events`: desde 6.2 se recargan al reconectar y cuando la pantalla que los cambia llama
  a `useDashboardRefresh()`, pero un cambio hecho desde otro navegador no se ve hasta
  recargar.
- [ ] No hay Dockerfile, ni Caddy/nginx, ni integración continua (CI).
- [ ] **Baja · Sin rastro de los cambios de cuenta.** Cambiar la contraseña, cambiar el nombre y
  "cerrar sesión en todos los dispositivos" (bloque 6.9) no quedan en `audit_logs`, porque esa
  tabla exige un negocio y estas acciones son de la persona, no de un negocio.
- [ ] **Baja · No hay "olvidé mi contraseña".** Hoy solo se cambia estando dentro; si alguien la
  olvida, hay que usar `scripts/crear_usuario.py --cambiar-clave`.
- [ ] **Baja · Sin limpieza de sesiones viejas.** Las sesiones vencidas o cerradas a propósito
  quedan en `refresh_tokens` para siempre.
- [ ] **Media · Consumo: las respuestas de IA dependen de `llm_runs`.** Se sacan de ahí (no de
  `usage_daily`); si algún día se aplica la retención de 90 días prevista para `llm_runs`, esa
  cifra desaparecería en los periodos viejos aunque los tokens sigan. Hay que guardarla también
  en `usage_daily` (columna nueva y migración) antes de borrar `llm_runs`.
- [ ] **Baja · Consumo: un envío sin respuesta de Evolution puede no contarse.** Si el envío
  queda sin respuesta (tiempo agotado) y el aviso `send.message` nunca llega, a los 10 minutos
  queda `failed` y no suma a "enviados", aunque quizá sí salió.
- [ ] **Baja · Consumo: no hay costo sin precios.** Sin `LLM_INPUT_USD_PER_MILLION` y
  `LLM_OUTPUT_USD_PER_MILLION` en el `.env` el panel solo muestra tokens. Y `audio_seconds`
  sigue en cero hasta el bloque 4.7.
- [ ] **Baja · Consumo: no se rellena el pasado.** Solo cuenta desde que se reinició la API y
  el worker con el bloque 6.10; lo anterior no tiene contadores.

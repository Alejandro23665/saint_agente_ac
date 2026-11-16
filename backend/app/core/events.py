"""Avisos en tiempo real para el panel (bloque 5.6).

Cuando algo cambia en un negocio (llega un mensaje, el bot contesta, alguien toma una
conversación, un número se conecta), la API o el worker publican un aviso corto en
Redis, en el canal de ese negocio. Cada panel abierto escucha su canal por /events y,
al recibir el aviso, vuelve a pedir a la API lo que cambió.

El aviso lleva solo el tipo y un id, nunca el texto de los mensajes: así nada privado
pasa por Redis y la API sigue decidiendo (con RLS y roles) qué puede ver cada persona.

Tipos de aviso:
- conversation.changed  {"conversation_id": "..."}  mensaje nuevo, respuesta, tomar,
                                                    devolver, asignar, cerrar
- inbox.changed         {}                          algo cambió en la bandeja (por
                                                    ejemplo, un mensaje pasó a entregado)
- numbers.changed       {}                          un número cambió de estado
- knowledge.changed     {"source_id": "..."}        un documento del conocimiento se
                                                    subió, quedó listo, falló o se borró
"""

import json
import logging
import uuid
from typing import Any

from redis.asyncio import Redis

logger = logging.getLogger(__name__)

CONVERSATION_CHANGED = "conversation.changed"
INBOX_CHANGED = "inbox.changed"
NUMBERS_CHANGED = "numbers.changed"
KNOWLEDGE_CHANGED = "knowledge.changed"


def channel(tenant_id: uuid.UUID) -> str:
    return f"saint:events:{tenant_id}"


async def publish(redis: Redis, tenant_id: uuid.UUID, event_type: str, **data: Any) -> None:
    """Publica el aviso. Si Redis falla, solo se anota: el cambio ya está guardado y el
    panel lo verá al recargar; un aviso perdido nunca debe romper la acción."""
    payload = json.dumps({"type": event_type, **{k: str(v) for k, v in data.items()}})
    try:
        await redis.publish(channel(tenant_id), payload)
    except Exception:
        logger.warning("No se pudo publicar el aviso %s del negocio %s", event_type, tenant_id)

"""Aviso connection.update de Evolution -> estado del número en channel_instances.

Evolution avisa cuando el número se conecta (open), se está conectando (connecting) o
se desconecta (close). Así el panel sabe, sin preguntar, si un número dejó de funcionar:
por ejemplo, si el dueño cerró la sesión desde su teléfono o WhatsApp lo bloqueó.
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChannelInstance, WebhookEvent
from app.models.enums import InstanceStatus
from app.services.channel_registration import apply_connection_state
from app.services.message_ingest import phone_from_jid

logger = logging.getLogger(__name__)


async def handle_connection_update(session: AsyncSession, event: WebhookEvent) -> None:
    data = event.payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("state"), str):
        return
    # FOR UPDATE: si llegan dos avisos de conexión a la vez, se aplican uno tras otro.
    instance = await session.scalar(
        select(ChannelInstance)
        .where(ChannelInstance.id == event.channel_instance_id)
        .with_for_update()
    )
    before = instance.status
    reason = data.get("statusReason")
    after = apply_connection_state(
        instance,
        data["state"],
        at=event.received_at,
        status_reason=reason if isinstance(reason, int) else None,
        phone_number=phone_from_jid(data.get("wuid")),
    )
    if after is None:
        logger.info("Aviso %s: estado atrasado o desconocido, se ignora", event.id)
    elif after != before:
        level = (
            logging.WARNING
            if after in (InstanceStatus.CLOSE, InstanceStatus.BANNED)
            else logging.INFO
        )
        logger.log(level, "Número %s: %s -> %s", instance.id, before, after)

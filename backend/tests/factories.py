"""Funciones para crear datos de prueba con pocos parámetros."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChannelInstance, Contact, Conversation, Tenant
from app.models.enums import Channel, ConversationStatus


def _suffix() -> str:
    return uuid.uuid4().hex[:8]


async def create_tenant(session: AsyncSession, name: str = "Negocio") -> Tenant:
    tenant = Tenant(name=name, slug=f"{name.lower()}-{_suffix()}", country_code="VE")
    session.add(tenant)
    await session.flush()
    return tenant


async def create_instance(session: AsyncSession, tenant: Tenant) -> ChannelInstance:
    instance = ChannelInstance(
        tenant_id=tenant.id,
        public_id=f"pub-{_suffix()}",
        channel=Channel.WHATSAPP,
        evolution_instance_name=f"inst-{_suffix()}",
    )
    session.add(instance)
    await session.flush()
    return instance


async def create_contact(
    session: AsyncSession, tenant: Tenant, external_id: str | None = None
) -> Contact:
    contact = Contact(
        tenant_id=tenant.id,
        channel=Channel.WHATSAPP,
        external_id=external_id or f"58412{_suffix()}@s.whatsapp.net",
        display_name="María Pérez",
    )
    session.add(contact)
    await session.flush()
    return contact


async def create_conversation(
    session: AsyncSession,
    tenant: Tenant,
    instance: ChannelInstance,
    contact: Contact,
    status: ConversationStatus = ConversationStatus.OPEN,
) -> Conversation:
    conversation = Conversation(
        tenant_id=tenant.id,
        channel_instance_id=instance.id,
        contact_id=contact.id,
        status=status,
    )
    session.add(conversation)
    await session.flush()
    return conversation

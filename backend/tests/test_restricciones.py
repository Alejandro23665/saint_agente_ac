"""Las reglas UNIQUE y CHECK de la base de datos rechazan datos duplicados o inválidos."""

import datetime as dt

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Contact,
    KnowledgeChunk,
    KnowledgeSource,
    Message,
    TenantMembership,
    UsageDaily,
    User,
    WebhookEvent,
)
from app.models.enums import (
    ConversationStatus,
    KnowledgeSourceType,
    MembershipRole,
    MessageDirection,
    MessageStatus,
    SenderType,
)
from tests.factories import (
    create_contact,
    create_conversation,
    create_instance,
    create_tenant,
)


async def test_correo_unico_sin_importar_mayusculas(owner_session: AsyncSession) -> None:
    owner_session.add(User(email="ana@saint.com", password_hash="x", full_name="Ana"))
    await owner_session.flush()
    owner_session.add(User(email="ANA@Saint.com", password_hash="x", full_name="Ana 2"))
    with pytest.raises(IntegrityError, match="uq_users_email"):
        await owner_session.flush()


async def test_un_usuario_una_vez_por_negocio(owner_session: AsyncSession) -> None:
    tenant = await create_tenant(owner_session)
    user = User(email="luis@saint.com", password_hash="x", full_name="Luis")
    owner_session.add(user)
    await owner_session.flush()
    for role in (MembershipRole.OWNER, MembershipRole.AGENT):
        owner_session.add(TenantMembership(tenant_id=tenant.id, user_id=user.id, role=role))
    with pytest.raises(IntegrityError, match="uq_tenant_memberships_tenant_id_user_id"):
        await owner_session.flush()


async def test_contacto_unico_por_negocio_y_canal(owner_session: AsyncSession) -> None:
    tenant = await create_tenant(owner_session)
    await create_contact(owner_session, tenant, external_id="584121234567@s.whatsapp.net")
    with pytest.raises(IntegrityError, match="uq_contacts_tenant_id_channel_external_id"):
        await create_contact(owner_session, tenant, external_id="584121234567@s.whatsapp.net")


async def test_mismo_telefono_en_dos_negocios_es_valido(owner_session: AsyncSession) -> None:
    jid = "584129999999@s.whatsapp.net"
    await create_contact(owner_session, await create_tenant(owner_session, "A"), external_id=jid)
    await create_contact(owner_session, await create_tenant(owner_session, "B"), external_id=jid)
    count = await owner_session.scalar(
        select(func.count()).select_from(Contact).where(Contact.external_id == jid)
    )
    assert count == 2


async def test_solo_una_conversacion_abierta(owner_session: AsyncSession) -> None:
    tenant = await create_tenant(owner_session)
    instance = await create_instance(owner_session, tenant)
    contact = await create_contact(owner_session, tenant)
    # Las cerradas pueden repetirse: son el historial.
    for _ in range(2):
        await create_conversation(
            owner_session, tenant, instance, contact, ConversationStatus.CLOSED
        )
    await create_conversation(owner_session, tenant, instance, contact)
    with pytest.raises(IntegrityError, match="uq_conversations_open_per_contact"):
        await create_conversation(owner_session, tenant, instance, contact)


async def test_mensaje_repetido_del_webhook_se_rechaza(owner_session: AsyncSession) -> None:
    tenant = await create_tenant(owner_session)
    instance = await create_instance(owner_session, tenant)
    conversation = await create_conversation(
        owner_session, tenant, instance, await create_contact(owner_session, tenant)
    )

    def message(external_id: str | None) -> Message:
        return Message(
            tenant_id=tenant.id,
            conversation_id=conversation.id,
            channel_instance_id=instance.id,
            external_id=external_id,
            direction=MessageDirection.INBOUND,
            sender_type=SenderType.CONTACT,
            status=MessageStatus.RECEIVED,
            text="Hola",
        )

    # Mensajes salientes aún sin id de WhatsApp (NULL) pueden ser varios.
    owner_session.add_all([message(None), message(None), message("3EB0ABC")])
    await owner_session.flush()
    owner_session.add(message("3EB0ABC"))
    with pytest.raises(IntegrityError, match="uq_messages_channel_instance_id_external_id"):
        await owner_session.flush()


async def test_evento_de_webhook_repetido_se_rechaza(owner_session: AsyncSession) -> None:
    tenant = await create_tenant(owner_session)
    instance = await create_instance(owner_session, tenant)
    for _ in range(2):
        owner_session.add(
            WebhookEvent(
                tenant_id=tenant.id,
                channel_instance_id=instance.id,
                event_type="messages.upsert",
                external_id="3EB0XYZ",
                payload={},
            )
        )
    with pytest.raises(IntegrityError, match="uq_webhook_events"):
        await owner_session.flush()


async def test_una_fila_de_consumo_por_dia(owner_session: AsyncSession) -> None:
    tenant = await create_tenant(owner_session)
    today = dt.date(2026, 11, 15)
    owner_session.add_all(
        [UsageDaily(tenant_id=tenant.id, day=today), UsageDaily(tenant_id=tenant.id, day=today)]
    )
    with pytest.raises(IntegrityError, match="uq_usage_daily_tenant_id_day"):
        await owner_session.flush()


async def test_debounce_fuera_de_rango(owner_session: AsyncSession) -> None:
    tenant = await create_tenant(owner_session)
    instance = await create_instance(owner_session, tenant)
    instance.debounce_seconds = 120
    with pytest.raises(IntegrityError, match="ck_channel_instances_debounce_range"):
        await owner_session.flush()


async def test_tsv_se_calcula_solo(owner_session: AsyncSession) -> None:
    tenant = await create_tenant(owner_session)
    source = KnowledgeSource(
        tenant_id=tenant.id,
        title="Preguntas frecuentes",
        source_type=KnowledgeSourceType.TEXT,
        checksum="abc",
    )
    owner_session.add(source)
    await owner_session.flush()
    chunk = KnowledgeChunk(
        tenant_id=tenant.id,
        source_id=source.id,
        chunk_index=0,
        content="Hacemos envíos a toda Venezuela",
        embedding=[0.01] * 1024,
    )
    owner_session.add(chunk)
    await owner_session.flush()
    await owner_session.refresh(chunk, ["tsv"])
    # El diccionario español reduce las palabras a su raíz: "envíos" -> "envi".
    assert "envi" in chunk.tsv
    assert chunk.meta == {}


async def test_ids_son_uuid_v7(owner_session: AsyncSession) -> None:
    tenant = await create_tenant(owner_session)
    # v7 empieza por la hora: los ids nuevos quedan ordenados y el índice crece al final.
    assert tenant.id.version == 7

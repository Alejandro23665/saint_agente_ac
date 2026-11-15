"""Tabla conversations: el hilo de chat entre un contacto y un número del negocio."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TenantOwned, Timestamps, UUIDPrimaryKey
from app.models.enums import ConversationMode, ConversationStatus, pg_enum


class Conversation(UUIDPrimaryKey, TenantOwned, Timestamps, Base):
    """Conversación. Solo puede haber una abierta por contacto y número."""

    __tablename__ = "conversations"
    __table_args__ = (
        # UNIQUE parcial: la regla solo aplica a las filas con status = 'open'.
        # Las cerradas pueden repetirse (historial).
        Index(
            "uq_conversations_open_per_contact",
            "channel_instance_id",
            "contact_id",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
        # Bandeja de entrada del panel: abiertas, la más reciente primero.
        Index(
            "ix_conversations_tenant_id_status_last_message_at",
            "tenant_id",
            "status",
            text("last_message_at DESC"),
        ),
        Index(
            "ix_conversations_followup",
            "tenant_id",
            postgresql_where=text("needs_followup"),
        ),
        Index("ix_conversations_contact_id", "contact_id"),
    )

    channel_instance_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("channel_instances.id", ondelete="RESTRICT")
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("contacts.id", ondelete="RESTRICT"))
    status: Mapped[ConversationStatus] = mapped_column(
        pg_enum(ConversationStatus, "conversation_status"),
        server_default=ConversationStatus.OPEN.value,
    )
    mode: Mapped[ConversationMode] = mapped_column(
        pg_enum(ConversationMode, "conversation_mode"), server_default=ConversationMode.BOT.value
    )
    assigned_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    active_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL")
    )
    needs_followup: Mapped[bool] = mapped_column(server_default="false")
    summary: Mapped[str | None] = mapped_column(Text)
    # conversations -> messages y messages -> conversations forman un ciclo.
    # use_alter crea esta FK después de ambas tablas, con un ALTER TABLE.
    summary_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL", use_alter=True)
    )
    last_inbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_outbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    messages: Mapped[list["Message"]] = relationship(  # noqa: F821
        back_populates="conversation",
        foreign_keys="Message.conversation_id",
        lazy="raise",
    )

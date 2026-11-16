"""Tabla handoff_events: historial de cambios de control entre bot y humano."""

import uuid

from sqlalchemy import ForeignKey, Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAt, TenantOwned, UUIDPrimaryKey
from app.models.enums import ConversationMode, HandoffReason, pg_enum


class HandoffEvent(UUIDPrimaryKey, TenantOwned, CreatedAt, Base):
    """Cada vez que la conversación pasa de bot a humano (o vuelve)."""

    __tablename__ = "handoff_events"
    __table_args__ = (
        Index("ix_handoff_events_conversation_id_created_at", "conversation_id", "created_at"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    # Reutiliza el mismo tipo ENUM de conversations.mode.
    from_mode: Mapped[ConversationMode] = mapped_column(
        pg_enum(ConversationMode, "conversation_mode")
    )
    to_mode: Mapped[ConversationMode] = mapped_column(
        pg_enum(ConversationMode, "conversation_mode")
    )
    reason: Mapped[HandoffReason] = mapped_column(pg_enum(HandoffReason, "handoff_reason"))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    note: Mapped[str | None] = mapped_column(Text)

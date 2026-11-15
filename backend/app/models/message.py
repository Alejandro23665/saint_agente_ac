"""Tabla messages: mensajes entrantes y salientes. También es la cola de envío."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TenantOwned, Timestamps, UUIDPrimaryKey
from app.models.enums import (
    ContentType,
    MessageDirection,
    MessageStatus,
    SenderType,
    TranscriptionStatus,
    pg_enum,
)


class Message(UUIDPrimaryKey, TenantOwned, Timestamps, Base):
    """Un mensaje. El UNIQUE de external_id evita guardar dos veces el mismo webhook."""

    __tablename__ = "messages"
    __table_args__ = (
        # NULL no choca con NULL en un UNIQUE: los salientes aún sin id externo conviven.
        UniqueConstraint("channel_instance_id", "external_id"),
        Index(
            "ix_messages_conversation_id_created_at",
            "conversation_id",
            text("created_at DESC"),
        ),
        # Lo que el bot aún no procesó (debouncing de 15 s).
        Index(
            "ix_messages_pending_inbound",
            "conversation_id",
            postgresql_where=text("direction = 'inbound' AND processed_at IS NULL"),
        ),
        # Cola de salida: mensajes por enviar.
        Index(
            "ix_messages_outbound_queue",
            "status",
            postgresql_where=text("status IN ('queued', 'sending')"),
        ),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    channel_instance_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("channel_instances.id", ondelete="RESTRICT")
    )
    external_id: Mapped[str | None] = mapped_column(Text)  # key.id de WhatsApp
    direction: Mapped[MessageDirection] = mapped_column(
        pg_enum(MessageDirection, "message_direction")
    )
    sender_type: Mapped[SenderType] = mapped_column(pg_enum(SenderType, "sender_type"))
    sender_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    group_participant_id: Mapped[str | None] = mapped_column(Text)
    content_type: Mapped[ContentType] = mapped_column(
        pg_enum(ContentType, "content_type"), server_default=ContentType.TEXT.value
    )
    text: Mapped[str | None] = mapped_column(Text)
    transcript: Mapped[str | None] = mapped_column(Text)  # texto de la nota de voz (Whisper)
    transcription_status: Mapped[TranscriptionStatus] = mapped_column(
        pg_enum(TranscriptionStatus, "transcription_status"),
        server_default=TranscriptionStatus.NONE.value,
    )
    media: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # {url R2, mime, tamaño}
    status: Mapped[MessageStatus] = mapped_column(pg_enum(MessageStatus, "message_status"))
    error: Mapped[str | None] = mapped_column(Text)
    reply_to_external_id: Mapped[str | None] = mapped_column(Text)
    # llm_runs se borra a los 90 días y los mensajes duran 12 meses: SET NULL.
    llm_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("llm_runs.id", ondelete="SET NULL")
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    external_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    conversation: Mapped["Conversation"] = relationship(  # noqa: F821
        back_populates="messages", foreign_keys=[conversation_id], lazy="raise"
    )

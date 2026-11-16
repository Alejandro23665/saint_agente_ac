"""Tabla webhook_events: cada evento crudo que manda Evolution API."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantOwned, UUIDPrimaryKey
from app.models.enums import WebhookEventStatus, pg_enum


class WebhookEvent(UUIDPrimaryKey, TenantOwned, Base):
    """Se guarda primero y se procesa después, en la cola ingest. Retención: 30 días."""

    __tablename__ = "webhook_events"
    __table_args__ = (
        # Evolution reintenta: si llega dos veces el mismo evento, el INSERT choca aquí.
        UniqueConstraint("channel_instance_id", "event_type", "external_id"),
        Index("ix_webhook_events_status_received_at", "status", "received_at"),
        Index("ix_webhook_events_received_at", "received_at"),  # para la limpieza de 30 días
    )

    channel_instance_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("channel_instances.id", ondelete="CASCADE")
    )
    event_type: Mapped[str] = mapped_column(Text)  # messages.upsert, connection.update...
    external_id: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[WebhookEventStatus] = mapped_column(
        pg_enum(WebhookEventStatus, "webhook_event_status"),
        server_default=WebhookEventStatus.PENDING.value,
    )
    attempts: Mapped[int] = mapped_column(server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

"""Tabla audit_logs: quién hizo qué en el panel."""

import uuid
from typing import Any

from sqlalchemy import ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAt, TenantOwned, UUIDPrimaryKey


class AuditLog(UUIDPrimaryKey, TenantOwned, CreatedAt, Base):
    """Registro de acciones. Solo se insertan filas; no se editan."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_tenant_id_created_at", "tenant_id", "created_at"),
        Index("ix_audit_logs_entity_type_entity_id", "entity_type", "entity_id"),
    )

    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(Text)  # "agent.update", "conversation.handoff"...
    entity_type: Mapped[str] = mapped_column(Text)
    entity_id: Mapped[uuid.UUID | None]
    diff: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ip: Mapped[str | None] = mapped_column(INET)

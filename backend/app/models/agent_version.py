"""Tabla agent_versions: historial de cada cambio de un agente."""

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import ForeignKey, Numeric, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedAt, UUIDPrimaryKey


class AgentVersion(UUIDPrimaryKey, CreatedAt, Base):
    """Foto de la configuración del agente en una versión.

    No tiene tenant_id: el negocio se deduce de su agente. La política RLS lo comprueba
    a través de agents.
    """

    __tablename__ = "agent_versions"
    __table_args__ = (UniqueConstraint("agent_id", "version"),)

    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"))
    version: Mapped[int]
    system_prompt: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    temperature: Mapped[Decimal] = mapped_column(Numeric(3, 2))
    tools: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    agent: Mapped["Agent"] = relationship(back_populates="versions", lazy="raise")  # noqa: F821

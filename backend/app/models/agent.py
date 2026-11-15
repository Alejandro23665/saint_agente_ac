"""Tabla agents: agentes de IA que configura cada negocio (router, faq, ventas...)."""

from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, Numeric, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TenantOwned, Timestamps, UUIDPrimaryKey
from app.models.enums import AgentKind, pg_enum


class Agent(UUIDPrimaryKey, TenantOwned, Timestamps, Base):
    """Configuración vigente de un agente. Los cambios se copian en agent_versions."""

    __tablename__ = "agents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "key"),
        CheckConstraint("temperature BETWEEN 0 AND 1", name="temperature_range"),
    )

    key: Mapped[str] = mapped_column(Text)  # "router", "faq"...
    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[AgentKind] = mapped_column(pg_enum(AgentKind, "agent_kind"))
    system_prompt: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text, server_default="claude-haiku-5-5")
    temperature: Mapped[Decimal] = mapped_column(Numeric(3, 2), server_default="0.30")
    tools: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")
    handoff_rules: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    is_active: Mapped[bool] = mapped_column(server_default="true")
    current_version: Mapped[int] = mapped_column(server_default="1")

    versions: Mapped[list["AgentVersion"]] = relationship(  # noqa: F821
        back_populates="agent", lazy="raise"
    )

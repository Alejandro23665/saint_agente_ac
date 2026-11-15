"""Tabla llm_runs: cada llamada al modelo de IA. Sirve para costes y depuración."""

import uuid
from typing import Any

from sqlalchemy import ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAt, TenantOwned, UUIDPrimaryKey
from app.models.enums import LLMRunStatus, pg_enum


class LLMRun(UUIDPrimaryKey, TenantOwned, CreatedAt, Base):
    """Una llamada a Claude Haiku. Retención: 90 días."""

    __tablename__ = "llm_runs"
    __table_args__ = (
        Index("ix_llm_runs_tenant_id_created_at", "tenant_id", "created_at"),
        Index("ix_llm_runs_conversation_id", "conversation_id"),
    )

    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id", ondelete="SET NULL"))
    agent_version: Mapped[int | None]
    model: Mapped[str] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(server_default="0")
    output_tokens: Mapped[int] = mapped_column(server_default="0")
    cached_tokens: Mapped[int] = mapped_column(server_default="0")
    latency_ms: Mapped[int | None]
    tool_calls: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")
    router_output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[LLMRunStatus] = mapped_column(pg_enum(LLMRunStatus, "llm_run_status"))
    error: Mapped[str | None] = mapped_column(Text)

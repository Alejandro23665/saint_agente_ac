"""Tabla usage_daily: consumo diario de cada negocio, base del cobro por consumo."""

from datetime import date

from sqlalchemy import BigInteger, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantOwned, Timestamps, UUIDPrimaryKey


class UsageDaily(UUIDPrimaryKey, TenantOwned, Timestamps, Base):
    """Una fila por negocio y día. Se suma con INSERT ... ON CONFLICT DO UPDATE."""

    __tablename__ = "usage_daily"
    __table_args__ = (UniqueConstraint("tenant_id", "day"),)

    day: Mapped[date]
    conversations: Mapped[int] = mapped_column(server_default="0")
    inbound_messages: Mapped[int] = mapped_column(server_default="0")
    outbound_messages: Mapped[int] = mapped_column(server_default="0")
    audio_seconds: Mapped[int] = mapped_column(server_default="0")
    llm_input_tokens: Mapped[int] = mapped_column(BigInteger, server_default="0")
    llm_output_tokens: Mapped[int] = mapped_column(BigInteger, server_default="0")

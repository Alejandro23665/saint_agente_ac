"""Tabla external_data_sources: conexión de solo lectura a la BD propia del cliente."""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, LargeBinary, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantOwned, Timestamps, UUIDPrimaryKey
from app.models.enums import ConnectionStatus, DataSourceEngine, pg_enum


class ExternalDataSource(UUIDPrimaryKey, TenantOwned, Timestamps, Base):
    """El bot solo puede ejecutar las consultas de allowed_queries, nunca SQL libre."""

    __tablename__ = "external_data_sources"

    name: Mapped[str] = mapped_column(Text)
    engine: Mapped[DataSourceEngine] = mapped_column(
        pg_enum(DataSourceEngine, "data_source_engine")
    )
    # Host, usuario y contraseña del cliente, cifrados con Fernet antes de guardarse.
    connection_encrypted: Mapped[bytes] = mapped_column(LargeBinary)
    allowed_queries: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")
    status: Mapped[ConnectionStatus] = mapped_column(
        pg_enum(ConnectionStatus, "connection_status"),
        server_default=ConnectionStatus.PENDING.value,
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

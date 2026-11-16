"""Tabla contacts: clientes finales (o grupos) que escriben al negocio."""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantOwned, Timestamps, UUIDPrimaryKey
from app.models.enums import Channel, pg_enum


class Contact(UUIDPrimaryKey, TenantOwned, Timestamps, Base):
    """Cliente final. El mismo teléfono en dos negocios son dos contactos distintos."""

    __tablename__ = "contacts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "channel", "external_id"),
        Index("ix_contacts_tenant_id_phone_e164", "tenant_id", "phone_e164"),
        # Búsqueda por nombre aproximado en el panel ("mari" encuentra "María").
        Index(
            "ix_contacts_display_name_trgm",
            "display_name",
            postgresql_using="gin",
            postgresql_ops={"display_name": "gin_trgm_ops"},
        ),
    )

    channel: Mapped[Channel] = mapped_column(pg_enum(Channel, "channel"))
    external_id: Mapped[str] = mapped_column(Text)  # remoteJid de WhatsApp o id de Instagram
    phone_e164: Mapped[str | None] = mapped_column(Text)
    display_name: Mapped[str | None] = mapped_column(Text)
    is_group: Mapped[bool] = mapped_column(server_default="false")
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    blocked: Mapped[bool] = mapped_column(server_default="false")
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

"""Tabla channel_instances: cada número de WhatsApp o cuenta de Instagram conectada."""

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Index, LargeBinary, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, SoftDelete, TenantOwned, Timestamps, UUIDPrimaryKey
from app.models.enums import Channel, ChannelProvider, InstanceStatus, pg_enum


class ChannelInstance(UUIDPrimaryKey, TenantOwned, Timestamps, SoftDelete, Base):
    """Número o cuenta conectada. Un negocio puede tener varios."""

    __tablename__ = "channel_instances"
    __table_args__ = (
        CheckConstraint("debounce_seconds BETWEEN 0 AND 60", name="debounce_range"),
        Index("ix_channel_instances_tenant_id_status", "tenant_id", "status"),
    )

    # Va en la URL del webhook (/webhooks/evolution/{public_id}); no revela el id interno.
    public_id: Mapped[str] = mapped_column(Text, unique=True)
    channel: Mapped[Channel] = mapped_column(pg_enum(Channel, "channel"))
    provider: Mapped[ChannelProvider] = mapped_column(
        pg_enum(ChannelProvider, "channel_provider"),
        server_default=ChannelProvider.EVOLUTION.value,
    )
    evolution_instance_name: Mapped[str | None] = mapped_column(Text, unique=True)
    display_identifier: Mapped[str | None] = mapped_column(Text)  # +58..., @usuario
    status: Mapped[InstanceStatus] = mapped_column(
        pg_enum(InstanceStatus, "instance_status"), server_default=InstanceStatus.CREATED.value
    )
    # Cifrado con Fernet en la aplicación; la BD solo ve bytes ilegibles.
    instance_token_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)
    webhook_secret_hash: Mapped[str | None] = mapped_column(Text)
    groups_enabled: Mapped[bool] = mapped_column(server_default="false")
    debounce_seconds: Mapped[int] = mapped_column(server_default="15")
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    last_connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Momento del aviso que fijó el estado actual: un aviso más viejo que llegue tarde
    # (por ejemplo, tras un reintento) no puede pisar un estado más nuevo.
    status_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    tenant: Mapped["Tenant"] = relationship(  # noqa: F821
        back_populates="channel_instances", lazy="raise"
    )

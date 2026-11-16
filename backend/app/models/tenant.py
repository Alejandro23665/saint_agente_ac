"""Tabla tenants: cada negocio cliente de Saint."""

from typing import Any

from sqlalchemy import CHAR, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, SoftDelete, Timestamps, UUIDPrimaryKey
from app.models.enums import BillingMode, pg_enum


class Tenant(UUIDPrimaryKey, Timestamps, SoftDelete, Base):
    """Negocio cliente de Saint (inquilino)."""

    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(Text)
    slug: Mapped[str] = mapped_column(Text, unique=True)
    country_code: Mapped[str] = mapped_column(CHAR(2))  # ISO 3166-1, ej. "VE"
    timezone: Mapped[str] = mapped_column(Text, server_default="America/Caracas")
    billing_mode: Mapped[BillingMode] = mapped_column(
        pg_enum(BillingMode, "billing_mode"), server_default=BillingMode.USAGE.value
    )
    plan: Mapped[str | None] = mapped_column(Text)
    business_hours: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    is_active: Mapped[bool] = mapped_column(server_default="true")

    members: Mapped[list["TenantMembership"]] = relationship(  # noqa: F821
        back_populates="tenant", lazy="raise"
    )
    channel_instances: Mapped[list["ChannelInstance"]] = relationship(  # noqa: F821
        back_populates="tenant", lazy="raise"
    )

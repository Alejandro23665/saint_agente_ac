"""Tabla tenant_memberships: une usuarios con negocios y dice qué rol tienen (N:M)."""

import uuid

from sqlalchemy import ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedAt, TenantOwned, UUIDPrimaryKey
from app.models.enums import MembershipRole, pg_enum


class TenantMembership(UUIDPrimaryKey, TenantOwned, CreatedAt, Base):
    """Rol de un usuario dentro de un negocio."""

    __tablename__ = "tenant_memberships"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id"),
        # Al iniciar sesión buscamos "¿a qué negocios pertenece este usuario?".
        Index("ix_tenant_memberships_user_id", "user_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[MembershipRole] = mapped_column(pg_enum(MembershipRole, "membership_role"))

    tenant: Mapped["Tenant"] = relationship(back_populates="members", lazy="raise")  # noqa: F821
    user: Mapped["User"] = relationship(back_populates="memberships", lazy="raise")  # noqa: F821

"""Tabla tenant_invitations: invitaciones para entrar a un negocio desde el panel.

El dueño (o un administrador) invita a un correo con un rol. El panel le da un enlace de
un solo uso que vence a las 72 horas, y la persona invitada pone su propia contraseña.
Como con las sesiones, la base guarda solo la huella (SHA-256) del enlace, no el enlace.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAt, TenantOwned, UUIDPrimaryKey
from app.models.enums import MembershipRole, pg_enum


class TenantInvitation(UUIDPrimaryKey, TenantOwned, CreatedAt, Base):
    """Invitación de un correo a un negocio, con el rol que tendrá al aceptarla."""

    __tablename__ = "tenant_invitations"
    __table_args__ = (
        # Una sola invitación abierta por correo y negocio: al invitar de nuevo, la anterior
        # se anula primero.
        Index(
            "uq_tenant_invitations_open",
            "tenant_id",
            "email",
            unique=True,
            postgresql_where=text("accepted_at IS NULL AND revoked_at IS NULL"),
        ),
    )

    email: Mapped[str] = mapped_column(CITEXT)
    role: Mapped[MembershipRole] = mapped_column(pg_enum(MembershipRole, "membership_role"))
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    invited_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

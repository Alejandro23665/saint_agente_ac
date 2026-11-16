"""Tabla users: personas que entran al panel. No lleva tenant_id."""

from datetime import datetime

from sqlalchemy import DateTime, Text
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, SoftDelete, Timestamps, UUIDPrimaryKey


class User(UUIDPrimaryKey, Timestamps, SoftDelete, Base):
    """Persona que entra al panel. Puede pertenecer a varios negocios."""

    __tablename__ = "users"

    # citext compara sin distinguir mayúsculas: Ana@x.com y ana@x.com son el mismo correo.
    email: Mapped[str] = mapped_column(CITEXT, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)  # hash argon2, nunca la contraseña
    full_name: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(server_default="true")
    is_platform_admin: Mapped[bool] = mapped_column(server_default="false")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    memberships: Mapped[list["TenantMembership"]] = relationship(  # noqa: F821
        back_populates="user", lazy="raise"
    )

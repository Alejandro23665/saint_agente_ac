"""Tabla refresh_tokens: sesiones abiertas del panel (renuevan el JWT de 15 minutos)."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAt, UUIDPrimaryKey


class RefreshToken(UUIDPrimaryKey, CreatedAt, Base):
    """Pase de renovación de 7 días. Guardamos solo su hash, no el pase."""

    __tablename__ = "refresh_tokens"
    __table_args__ = (Index("ix_refresh_tokens_user_id_expires_at", "user_id", "expires_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(INET)

"""Clase base de los modelos y piezas comunes a varias tablas."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, MetaData, func, text
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

from app.db.ids import uuid7

# Dimensión de los embeddings. Nemotron devuelve 4096 números y los recortamos a 1024
# (igual que Voyage), porque los índices de pgvector admiten como máximo 2000.
EMBEDDING_DIM = 1024

# Convención de nombres para índices y restricciones. Así Alembic genera siempre
# los mismos nombres y las migraciones futuras pueden encontrarlos.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UUIDPrimaryKey:
    """Clave primaria UUID v7 generada en Python.

    El default del servidor (gen_random_uuid, v4) solo se usa si alguien inserta
    filas a mano con SQL sin dar un id.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid7,
        server_default=text("gen_random_uuid()"),
        sort_order=-10,  # que id sea la primera columna de la tabla
    )


class CreatedAt:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), sort_order=100
    )


class Timestamps(CreatedAt):
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), sort_order=101
    )


class SoftDelete:
    """Borrado lógico: la fila se conserva y se marca con la fecha de borrado."""

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), sort_order=102)


class TenantOwned:
    """Tabla cuyas filas pertenecen a un negocio. Estas tablas llevan RLS."""

    @declared_attr
    def tenant_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(
            ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, sort_order=-9
        )

"""Tabla catalog_items: productos y servicios del negocio (cuando usa la BD de Saint)."""

from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import CHAR, CheckConstraint, Index, Numeric, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import EMBEDDING_DIM, Base, TenantOwned, Timestamps, UUIDPrimaryKey
from app.models.enums import CatalogItemType, pg_enum


class CatalogItem(UUIDPrimaryKey, TenantOwned, Timestamps, Base):
    """Producto o servicio. El bot lo busca por nombre o por significado."""

    __tablename__ = "catalog_items"
    __table_args__ = (
        UniqueConstraint("tenant_id", "sku"),
        CheckConstraint("price >= 0", name="price_not_negative"),
        Index("ix_catalog_items_tenant_id_is_active", "tenant_id", "is_active"),
        Index(
            "ix_catalog_items_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index(
            "ix_catalog_items_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
    )

    sku: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    item_type: Mapped[CatalogItemType] = mapped_column(
        pg_enum(CatalogItemType, "catalog_item_type"),
        server_default=CatalogItemType.PRODUCT.value,
    )
    # Numeric y no float: el dinero no debe tener errores de redondeo.
    price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(CHAR(3), server_default="USD")
    stock: Mapped[int | None]  # NULL = no se controla stock (ej. servicios)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    embedding_model: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(server_default="true")

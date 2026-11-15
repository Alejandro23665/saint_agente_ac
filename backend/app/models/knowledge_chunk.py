"""Tabla knowledge_chunks: fragmentos de documentos con su embedding (RAG)."""

import uuid
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import Computed, ForeignKey, Index, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import EMBEDDING_DIM, Base, CreatedAt, TenantOwned, UUIDPrimaryKey


class KnowledgeChunk(UUIDPrimaryKey, TenantOwned, CreatedAt, Base):
    """Un trozo de ~500 tokens. Se busca por significado (embedding) y por palabras (tsv)."""

    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("source_id", "chunk_index"),
        Index("ix_knowledge_chunks_tenant_id", "tenant_id"),
        # HNSW: índice para encontrar rápido los vectores más parecidos (distancia coseno).
        Index(
            "ix_knowledge_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_knowledge_chunks_tsv", "tsv", postgresql_using="gin"),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_sources.id", ondelete="CASCADE")
    )
    chunk_index: Mapped[int]
    content: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int | None]
    # Nullable: el fragmento existe antes de calcular su embedding, y al cambiar a Voyage
    # se vacía y se recalcula.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    embedding_model: Mapped[str | None] = mapped_column(Text)  # ej. "nvidia/nemotron-..."
    # Columna generada: PostgreSQL la calcula sola a partir de content.
    tsv: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('spanish', content)", persisted=True)
    )
    # "metadata" está reservado en SQLAlchemy; en Python se llama meta.
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, server_default="{}")

    source: Mapped["KnowledgeSource"] = relationship(  # noqa: F821
        back_populates="chunks", lazy="raise"
    )

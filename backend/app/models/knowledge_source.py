"""Tabla knowledge_sources: documentos que el negocio sube para el RAG."""

import uuid

from sqlalchemy import ForeignKey, Index, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TenantOwned, Timestamps, UUIDPrimaryKey
from app.models.enums import KnowledgeSourceType, ProcessingStatus, pg_enum


class KnowledgeSource(UUIDPrimaryKey, TenantOwned, Timestamps, Base):
    """Documento de origen. Se parte en fragmentos (knowledge_chunks)."""

    __tablename__ = "knowledge_sources"
    __table_args__ = (
        # El mismo archivo subido dos veces por el mismo negocio choca aquí.
        UniqueConstraint("tenant_id", "checksum"),
        Index("ix_knowledge_sources_tenant_id_status", "tenant_id", "status"),
    )

    title: Mapped[str] = mapped_column(Text)
    source_type: Mapped[KnowledgeSourceType] = mapped_column(
        pg_enum(KnowledgeSourceType, "knowledge_source_type")
    )
    file_url: Mapped[str | None] = mapped_column(Text)  # ruta en Cloudflare R2 o URL
    checksum: Mapped[str] = mapped_column(Text)  # sha256 del contenido
    status: Mapped[ProcessingStatus] = mapped_column(
        pg_enum(ProcessingStatus, "processing_status"),
        server_default=ProcessingStatus.PENDING.value,
    )
    error: Mapped[str | None] = mapped_column(Text)
    chunk_count: Mapped[int] = mapped_column(server_default="0")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    chunks: Mapped[list["KnowledgeChunk"]] = relationship(  # noqa: F821
        back_populates="source", lazy="raise"
    )

"""Knowledge documents, chunks, and ingest job observability models."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from education_platform.db.base import Base, UUIDTimestampMixin
from education_platform.db.types import JsonDict, partial_unique_index, str_enum


class KnowledgeDocumentStatus(str, enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class KnowledgeDocumentVersionStatus(str, enum.Enum):
    DRAFT = "draft"
    PROCESSING = "processing"
    READY = "ready"
    PUBLISHED = "published"
    FAILED = "failed"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"


class IngestJobStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class KnowledgeDocument(UUIDTimestampMixin, Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (
        UniqueConstraint(
            "institution_id",
            "slug",
            name="uq_knowledge_documents_institution_slug",
        ),
    )

    institution_id: Mapped[UUID] = mapped_column(ForeignKey("institutions.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(100))
    doc_type: Mapped[str] = mapped_column(String(50), index=True)
    status: Mapped[KnowledgeDocumentStatus] = mapped_column(
        str_enum(KnowledgeDocumentStatus, "knowledge_document_status"),
        default=KnowledgeDocumentStatus.DRAFT,
        index=True,
    )
    required_roles: Mapped[list[Any]] = mapped_column(
        JsonDict,
        default=lambda: ["administrator", "teacher"],
    )


class KnowledgeDocumentVersion(UUIDTimestampMixin, Base):
    __tablename__ = "knowledge_document_versions"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "version_number",
            name="uq_knowledge_document_versions_document_version",
        ),
        partial_unique_index(
            "uq_knowledge_document_versions_one_published",
            "document_id",
            where="lifecycle_status = 'published'",
        ),
        CheckConstraint(
            "version_number >= 1",
            name="ck_knowledge_document_versions_version_number",
        ),
        CheckConstraint(
            "stage IS NULL OR stage IN ("
            "'received', 'parsing', 'chunking', 'embedding', "
            "'extracting_topics', 'done', 'failed')",
            name="ck_knowledge_document_versions_stage",
        ),
        CheckConstraint(
            "stage_current IS NULL OR stage_current >= 0",
            name="ck_knowledge_document_versions_stage_current",
        ),
        CheckConstraint(
            "stage_total IS NULL OR stage_total >= 0",
            name="ck_knowledge_document_versions_stage_total",
        ),
        CheckConstraint("stage_seq >= 0", name="ck_knowledge_document_versions_stage_seq"),
    )

    document_id: Mapped[UUID] = mapped_column(ForeignKey("knowledge_documents.id"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    lifecycle_status: Mapped[KnowledgeDocumentVersionStatus] = mapped_column(
        str_enum(KnowledgeDocumentVersionStatus, "knowledge_document_version_status"),
        default=KnowledgeDocumentVersionStatus.DRAFT,
        index=True,
    )
    blob_object_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    blob_content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    stage_current: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stage_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stage_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    stage_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    stage_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class KnowledgeChunk(UUIDTimestampMixin, Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint(
            "knowledge_document_version_id",
            "content_hash",
            name="uq_knowledge_chunks_version_content_hash",
        ),
        CheckConstraint("ordinal >= 1", name="ck_knowledge_chunks_ordinal"),
        CheckConstraint(
            "token_count IS NULL OR token_count >= 0",
            name="ck_knowledge_chunks_token_count",
        ),
    )

    knowledge_document_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_document_versions.id"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section_heading: Mapped[str | None] = mapped_column(String(500), nullable=True)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)


class IngestJob(UUIDTimestampMixin, Base):
    """Work-queue row. Target is an "exclusive arc": exactly one of the two FK
    columns below is set, and it alone tells you what kind of job this is —
    no separate discriminator column needed.
    """

    __tablename__ = "ingest_jobs"
    __table_args__ = (
        CheckConstraint(
            "(source_material_version_id IS NOT NULL) != "
            "(knowledge_document_version_id IS NOT NULL)",
            name="ck_ingest_jobs_exactly_one_target",
        ),
        Index("ix_ingest_jobs_status_created_at", "status", "created_at"),
    )

    source_material_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("source_material_versions.id"), nullable=True, index=True
    )
    knowledge_document_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("knowledge_document_versions.id"), nullable=True, index=True
    )
    status: Mapped[IngestJobStatus] = mapped_column(
        str_enum(IngestJobStatus, "ingest_job_status"),
        default=IngestJobStatus.QUEUED,
        index=True,
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default="3"
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ChunkEmbedding(Base):
    """pgvector row for a source/knowledge chunk embedding."""

    __tablename__ = "chunk_embeddings"
    __table_args__ = (
        CheckConstraint(
            "doc_kind IN ('source_material_version', 'knowledge_document_version')",
            name="ck_chunk_embeddings_doc_kind",
        ),
    )

    chunk_id: Mapped[UUID] = mapped_column(primary_key=True)
    embedding: Mapped[list[float]] = mapped_column(Vector(384))
    doc_id: Mapped[UUID] = mapped_column()
    doc_kind: Mapped[str] = mapped_column(String(50))
    institution_id: Mapped[UUID] = mapped_column(index=True)
    required_roles: Mapped[list[Any]] = mapped_column(JsonDict)
    doc_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    version_id: Mapped[UUID] = mapped_column(index=True)

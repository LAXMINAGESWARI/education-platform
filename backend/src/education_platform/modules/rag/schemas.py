"""Pydantic schemas for admin RAG ingest APIs."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class MaterialIngestAccepted(BaseModel):
    source_material_id: UUID
    version_id: UUID
    version_number: int
    lifecycle_status: str
    title: str
    ingest_job_id: UUID


class MaterialVersionStatusOut(BaseModel):
    id: UUID
    source_material_id: UUID
    version_number: int
    title: str
    lifecycle_status: str
    failure_reason: str | None
    chunk_count: int
    blob_content_type: str | None = None
    stage: str | None = None
    stage_seq: int = 0
    stage_current: int | None = None
    stage_total: int | None = None
    stage_message: str | None = None
    stage_updated_at: datetime | None = None


class KnowledgeDocumentVersionSummary(BaseModel):
    id: UUID
    version_number: int
    lifecycle_status: str
    failure_reason: str | None = None
    chunk_count: int = 0
    created_at: datetime | None = None


class KnowledgeDocumentOut(BaseModel):
    id: UUID
    title: str
    slug: str
    doc_type: str
    status: str
    required_roles: list[str]
    latest_version: KnowledgeDocumentVersionSummary | None = None


class KnowledgeDocumentDetailOut(BaseModel):
    id: UUID
    title: str
    slug: str
    doc_type: str
    status: str
    required_roles: list[str]
    versions: list[KnowledgeDocumentVersionSummary] = Field(default_factory=list)


class KnowledgeIngestAccepted(BaseModel):
    document_id: UUID
    version_id: UUID
    version_number: int
    lifecycle_status: str
    title: str
    doc_type: str
    ingest_job_id: UUID


class KnowledgeVersionStatusOut(BaseModel):
    id: UUID
    document_id: UUID
    version_number: int
    lifecycle_status: str
    failure_reason: str | None
    chunk_count: int
    blob_content_type: str | None = None
    stage: str | None = None
    stage_seq: int = 0
    stage_current: int | None = None
    stage_total: int | None = None
    stage_message: str | None = None
    stage_updated_at: datetime | None = None

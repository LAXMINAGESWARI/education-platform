"""Shared source-material PDF enqueue for admin ingest and topic generation."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from education_platform.core.config import get_settings
from education_platform.core.errors import DomainError
from education_platform.modules.materials.models import (
    SourceMaterial,
    SourceMaterialStatus,
    SourceMaterialVersion,
    SourceMaterialVersionStatus,
)
from education_platform.modules.progress.stage_store import mark_upload_received
from education_platform.modules.rag import storage
from education_platform.modules.rag.models import IngestJob, IngestJobStatus


@dataclass(frozen=True, slots=True)
class QueuedSourceMaterialPdf:
    version: SourceMaterialVersion
    job: IngestJob


def validate_pdf_upload(file: UploadFile, data: bytes) -> str:
    settings = get_settings()
    content_type = (file.content_type or "").split(";")[0].strip().lower() or "application/pdf"
    if content_type not in settings.ingest_allowed_content_types:
        raise DomainError(
            f"Unsupported content type: {content_type}",
            status_code=400,
        )
    if len(data) == 0:
        raise DomainError("Empty upload", status_code=400)
    if len(data) > settings.max_upload_bytes:
        raise DomainError(
            f"File exceeds max size of {settings.max_upload_bytes} bytes",
            status_code=400,
        )
    return content_type


async def _insert_processing_version(
    session: AsyncSession,
    *,
    material: SourceMaterial,
    title: str,
    data: bytes,
    content_type: str,
    object_key: str,
    submitted_by_user_id: UUID | None,
) -> QueuedSourceMaterialPdf:
    next_version = await session.scalar(
        select(func.coalesce(func.max(SourceMaterialVersion.version_number), 0)).where(
            SourceMaterialVersion.source_material_id == material.id
        )
    )
    version = SourceMaterialVersion(
        source_material_id=material.id,
        version_number=int(next_version or 0) + 1,
        lifecycle_status=SourceMaterialVersionStatus.PROCESSING,
        title=title,
        content_format="pdf",
        blob_object_key=object_key,
        blob_content_type=content_type,
        checksum=storage.sha256_hex(data),
        submitted_by_user_id=submitted_by_user_id,
    )
    mark_upload_received(version)
    session.add(version)
    await session.flush()

    job = IngestJob(
        id=uuid4(),
        source_material_version_id=version.id,
        status=IngestJobStatus.QUEUED,
    )
    session.add(job)
    await session.flush()
    await session.refresh(version)
    await session.refresh(job)
    return QueuedSourceMaterialPdf(version=version, job=job)


async def queue_source_material_pdf(
    session: AsyncSession,
    *,
    subtopic_id: UUID,
    title: str,
    data: bytes,
    content_type: str,
    object_key: str,
    submitted_by_user_id: UUID | None,
) -> QueuedSourceMaterialPdf:
    """Find-or-create slug ``lesson``, insert a PROCESSING version, enqueue ingest.

    ``submitted_by_user_id`` None is admin index-only.
    Caller stores the blob before this call.
    """
    material = await session.scalar(
        select(SourceMaterial).where(
            SourceMaterial.subtopic_id == subtopic_id,
            SourceMaterial.slug == "lesson",
        )
    )
    stripped = title.strip() or "Lesson"
    if material is None:
        material = SourceMaterial(
            subtopic_id=subtopic_id,
            title=stripped,
            slug="lesson",
            status=SourceMaterialStatus.DRAFT,
        )
        session.add(material)
        await session.flush()

    return await _insert_processing_version(
        session,
        material=material,
        title=stripped,
        data=data,
        content_type=content_type,
        object_key=object_key,
        submitted_by_user_id=submitted_by_user_id,
    )


async def queue_topic_intake_pdf(
    session: AsyncSession,
    *,
    topic_id: UUID,
    title: str,
    data: bytes,
    content_type: str,
    object_key: str,
    submitted_by_user_id: UUID,
) -> QueuedSourceMaterialPdf:
    """Find-or-create topic-scoped slug='source'. PROCESSING version.

    Caller stored the blob.
    """
    material = await session.scalar(
        select(SourceMaterial).where(
            SourceMaterial.topic_id == topic_id,
            SourceMaterial.slug == "source",
        )
    )
    stripped = title.strip() or "Topic source"
    if material is None:
        material = SourceMaterial(
            topic_id=topic_id,
            title=stripped,
            slug="source",
            status=SourceMaterialStatus.DRAFT,
        )
        session.add(material)
        await session.flush()

    return await _insert_processing_version(
        session,
        material=material,
        title=stripped,
        data=data,
        content_type=content_type,
        object_key=object_key,
        submitted_by_user_id=submitted_by_user_id,
    )

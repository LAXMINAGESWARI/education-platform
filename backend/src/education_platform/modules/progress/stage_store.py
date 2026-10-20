"""Persist upload stage rows and clear them when a lease is no longer live."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from education_platform.db.session import sync_session
from education_platform.modules.generation.models import ContentGenerationRun
from education_platform.modules.materials.models import (
    SourceMaterialVersion,
    SourceMaterialVersionStatus,
)
from education_platform.modules.progress.events import (
    RECEIVED_MESSAGE,
    RESTART_MESSAGE,
    same_stage_state,
    should_persist_stage,
)
from education_platform.modules.progress.types import ProgressSubject, run_subject, version_subject
from education_platform.modules.progress.wake import publish_wake, publish_wake_async
from education_platform.modules.rag.models import (
    IngestJob,
    IngestJobStatus,
    KnowledgeDocumentVersion,
    KnowledgeDocumentVersionStatus,
)

type VersionRow = SourceMaterialVersion | KnowledgeDocumentVersion
_RESTART_STAGE = "received"


def persist_upload_stage(
    version: VersionRow,
    job: IngestJob,
    *,
    stage: str,
    message: str | None,
    current: int | None = None,
    total: int | None = None,
    now: datetime | None = None,
    force: bool = False,
) -> int | None:
    """Commit one stage on its own session. Returns the new seq, or None when throttled.

    Chunk and vector writes stay on the caller's session. Identical state is not
    written again, so a forced boundary does not burn a seq.
    """
    moment = now or datetime.now(UTC)
    own = sync_session()
    try:
        row = _lock_version(own, version)
        if row is None:
            return None
        if same_stage_state(
            previous_stage=row.stage,
            previous_current=row.stage_current,
            previous_total=row.stage_total,
            previous_message=row.stage_message,
            stage=stage,
            current=current,
            total=total,
            message=message,
        ):
            return None
        if not force and not should_persist_stage(
            previous_stage=row.stage,
            previous_current=row.stage_current,
            previous_total=row.stage_total,
            previous_updated_at=row.stage_updated_at,
            stage=stage,
            current=current,
            total=total,
            now=moment,
        ):
            return None
        row.stage = stage
        row.stage_current = current
        row.stage_total = total
        row.stage_message = message
        row.stage_updated_at = moment
        row.stage_seq = int(row.stage_seq or 0) + 1
        seq = int(row.stage_seq)
        publish_wake(own, *_subjects_sync(own, job))
        own.commit()
        return seq
    except Exception:
        own.rollback()
        raise
    finally:
        own.close()


def commit_upload_terminal(
    session: Session,
    version: VersionRow,
    job: IngestJob,
    *,
    stage: str,
    lifecycle: SourceMaterialVersionStatus | KnowledgeDocumentVersionStatus,
    failure_reason: str | None,
    job_status: IngestJobStatus,
    job_error: str | None,
    message: str | None,
    now: datetime | None = None,
) -> None:
    """Store the terminal stage in the same transaction as lifecycle and the job row."""
    model = type(version)
    moment = now or datetime.now(UTC)
    session.execute(
        update(model)
        .where(model.id == version.id)
        .values(
            lifecycle_status=lifecycle,
            failure_reason=failure_reason,
            stage=stage,
            stage_current=None,
            stage_total=None,
            stage_message=message,
            stage_updated_at=moment,
            stage_seq=model.stage_seq + 1,
        )
    )
    job.status = job_status
    job.error = job_error
    publish_wake(session, *_subjects_sync(session, job))
    session.commit()


def reset_stage_on_requeue(session: Session, job: Any) -> None:
    """Drop a stale in-flight stage. Does not commit.

    A ready or failed version is left alone. Repeating the reset while the row
    already says ``Restarting...`` does not advance ``stage_seq``.

    A lease release must call this only when that release updated a row
    (rowcount 1). A no-op release — the lease token did not match because
    another worker reclaimed the job — must not call it, or the live worker's
    stage would be cleared. This function does not inspect a release token.
    """
    statement = _restart_statement(job)
    if statement is None:
        return
    result = session.execute(statement)
    if _rowcount(result) > 0:
        publish_wake(session, *_subjects_sync(session, job))


def ingest_lease_needs_stage_reset(job: IngestJob, *, now: datetime | None = None) -> bool:
    """True when a live worker is not holding this job and its stage may be stale.

    A first queue has ``attempts == 0`` and is left alone. A running job with a
    missing or expired lease matches. A queued job that already has attempts
    matches a requeue that kept the attempt count. PR #200's release (2a00407)
    refunds a graceful SIGTERM and is a no-op unless id, running, attempts, and
    the last ``locked_until`` match. That path is not detected here; it calls
    ``reset_stage_on_requeue`` itself only when its update rowcount is 1.
    """
    moment = now or datetime.now(UTC)
    if job.status is IngestJobStatus.RUNNING:
        if job.locked_until is None:
            return True
        return _as_utc(job.locked_until) < moment
    if job.status is IngestJobStatus.QUEUED:
        return int(job.attempts or 0) >= 1
    return False


async def heal_expired_ingest_stage(session: AsyncSession, version_id: UUID) -> None:
    """Restart the stage when the active ingest job was released or its lease expired."""
    job = await session.scalar(
        select(IngestJob)
        .where(
            or_(
                IngestJob.source_material_version_id == version_id,
                IngestJob.knowledge_document_version_id == version_id,
            ),
            IngestJob.status.in_((IngestJobStatus.QUEUED, IngestJobStatus.RUNNING)),
        )
        .order_by(IngestJob.created_at.desc())
        .limit(1)
    )
    if job is None or not ingest_lease_needs_stage_reset(job):
        return
    statement = _restart_statement(job)
    if statement is None:
        return
    result = await session.execute(statement)
    if _rowcount(result) > 0:
        await publish_wake_async(session, *_subjects_sync_ids(job, await _run_id(session, job)))


def mark_upload_received(version: VersionRow, *, now: datetime | None = None) -> None:
    """Record the queued file on the version before the worker starts."""
    version.stage = "received"
    version.stage_current = None
    version.stage_total = None
    version.stage_message = RECEIVED_MESSAGE
    version.stage_seq = 1
    version.stage_updated_at = now or datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _restart_statement(job: Any) -> Any:
    target = _version_target(job)
    if target is None:
        return None
    model, version_id = target
    return (
        update(model)
        .where(
            model.id == version_id,
            model.lifecycle_status.in_(_open_statuses(model)),
            or_(
                model.stage.is_distinct_from(_RESTART_STAGE),
                model.stage_message.is_distinct_from(RESTART_MESSAGE),
            ),
        )
        .values(
            stage=_RESTART_STAGE,
            stage_current=None,
            stage_total=None,
            stage_message=RESTART_MESSAGE,
            stage_updated_at=datetime.now(UTC),
            stage_seq=model.stage_seq + 1,
        )
    )


def _lock_version(session: Session, version: VersionRow) -> VersionRow | None:
    if isinstance(version, SourceMaterialVersion):
        return session.get(SourceMaterialVersion, version.id, with_for_update=True)
    return session.get(KnowledgeDocumentVersion, version.id, with_for_update=True)


def _rowcount(result: object) -> int:
    if isinstance(result, CursorResult):
        return int(result.rowcount or 0)
    return 0


def _version_target(job: Any) -> tuple[type[VersionRow], UUID] | None:
    source_id = getattr(job, "source_material_version_id", None)
    if source_id is not None:
        return SourceMaterialVersion, source_id
    knowledge_id = getattr(job, "knowledge_document_version_id", None)
    if knowledge_id is not None:
        return KnowledgeDocumentVersion, knowledge_id
    return None


def _open_statuses(
    model: type[VersionRow],
) -> tuple[SourceMaterialVersionStatus | KnowledgeDocumentVersionStatus, ...]:
    if model is SourceMaterialVersion:
        return (
            SourceMaterialVersionStatus.DRAFT,
            SourceMaterialVersionStatus.PROCESSING,
        )
    return (
        KnowledgeDocumentVersionStatus.DRAFT,
        KnowledgeDocumentVersionStatus.PROCESSING,
    )


def _subjects_sync(session: Session, job: Any) -> list[ProgressSubject]:
    return _subjects_sync_ids(job, _run_id_sync(session, job))


def _subjects_sync_ids(job: Any, run_id: UUID | None) -> list[ProgressSubject]:
    subjects: list[ProgressSubject] = []
    source_id = getattr(job, "source_material_version_id", None)
    knowledge_id = getattr(job, "knowledge_document_version_id", None)
    version_id = source_id or knowledge_id
    if isinstance(version_id, UUID):
        subjects.append(version_subject(version_id))
    if run_id is not None:
        subjects.append(run_subject(run_id))
    return subjects


def _run_id_sync(session: Session, job: Any) -> UUID | None:
    source_id = getattr(job, "source_material_version_id", None)
    if not isinstance(source_id, UUID):
        return None
    found = session.scalar(
        select(ContentGenerationRun.id).where(
            ContentGenerationRun.intake_source_material_version_id == source_id
        )
    )
    return found if isinstance(found, UUID) else None


async def _run_id(session: AsyncSession, job: Any) -> UUID | None:
    source_id = getattr(job, "source_material_version_id", None)
    if not isinstance(source_id, UUID):
        return None
    found = await session.scalar(
        select(ContentGenerationRun.id).where(
            ContentGenerationRun.intake_source_material_version_id == source_id
        )
    )
    return found if isinstance(found, UUID) else None

"""Persist upload stage rows and clear them when a lease is no longer live."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

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
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class StageClaim:
    """Claim identity a stage write must still match.

    ``attempts`` is the value ``claim()`` stored for this worker. ``locked_until``
    is the lease token. Leave it unset until that token is passed in. When it is
    set, the job row's ``locked_until`` must be the same instant.
    """

    attempts: int
    locked_until: datetime | None = None


def stage_claim_matches_job(
    *,
    job_status: str | None,
    job_attempts: int,
    job_locked_until: datetime | None,
    claim: StageClaim,
) -> bool:
    """True when this worker still owns the running job.

    The ``locked_until`` comparison is the slot for the #200 lease token. A
    claim with no token checks status and attempts only.
    """
    if job_status != IngestJobStatus.RUNNING.value:
        return False
    if int(job_attempts) != claim.attempts:
        return False
    if claim.locked_until is None:
        return True
    if job_locked_until is None:
        return False
    return _as_utc(job_locked_until) == _as_utc(claim.locked_until)


@dataclass(frozen=True, slots=True)
class EffectiveIngestStage:
    """Stage fields a status or stream read should return."""

    stage: str | None
    stage_seq: int
    stage_current: int | None
    stage_total: int | None
    stage_message: str | None
    stage_updated_at: datetime | None


def _memory_says_skip(
    version: VersionRow,
    *,
    stage: str,
    message: str | None,
    current: int | None,
    total: int | None,
    now: datetime,
    force: bool,
) -> bool:
    return _row_says_skip(
        version,
        stage=stage,
        message=message,
        current=current,
        total=total,
        now=now,
        force=force,
    )


def _row_says_skip(
    row: VersionRow,
    *,
    stage: str,
    message: str | None,
    current: int | None,
    total: int | None,
    now: datetime,
    force: bool,
) -> bool:
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
        return True
    if force:
        return False
    return not should_persist_stage(
        previous_stage=row.stage,
        previous_current=row.stage_current,
        previous_total=row.stage_total,
        previous_updated_at=row.stage_updated_at,
        stage=stage,
        current=current,
        total=total,
        now=now,
    )


def _remember_stage(version: VersionRow, row: VersionRow) -> None:
    """Copy the committed stage onto the caller's object without marking it dirty."""
    set_committed_value(version, "stage", row.stage)
    set_committed_value(version, "stage_current", row.stage_current)
    set_committed_value(version, "stage_total", row.stage_total)
    set_committed_value(version, "stage_message", row.stage_message)
    set_committed_value(version, "stage_updated_at", row.stage_updated_at)
    set_committed_value(version, "stage_seq", row.stage_seq)


def _job_status_value(status: Any) -> str:
    value = status.value if isinstance(status, IngestJobStatus) else status
    return str(value)


def persist_upload_stage(
    version: VersionRow,
    job: IngestJob,
    *,
    claim: StageClaim,
    stage: str,
    message: str | None,
    current: int | None = None,
    total: int | None = None,
    now: datetime | None = None,
    force: bool = False,
) -> int | None:
    """Commit one stage on its own session. Returns the new seq, or None when skipped.

    The write lands only while the job row is ``running`` and ``attempts`` equals
    ``claim.attempts``. A mismatch is logged and skipped, so a worker that lost
    the lease cannot replace the stage of the worker that reclaimed it. Pass
    ``claim.locked_until`` to require that token as well.

    Chunk and vector writes stay on the caller's session. Identical state is not
    written again, so a forced boundary does not burn a seq. The throttle is
    checked on the in-memory version before a row lock.
    """
    moment = now or datetime.now(UTC)
    if _memory_says_skip(
        version,
        stage=stage,
        message=message,
        current=current,
        total=total,
        now=moment,
        force=force,
    ):
        return None
    own = sync_session()
    try:
        job_row = own.get(IngestJob, job.id, with_for_update={"read": True})
        if job_row is None or not stage_claim_matches_job(
            job_status=_job_status_value(job_row.status),
            job_attempts=int(job_row.attempts or 0),
            job_locked_until=job_row.locked_until,
            claim=claim,
        ):
            logger.info(
                "Skipped stage write for ingest job %s: claim attempts=%s locked_until=%s; "
                "row status=%s attempts=%s locked_until=%s",
                job.id,
                claim.attempts,
                claim.locked_until,
                None if job_row is None else _job_status_value(job_row.status),
                None if job_row is None else job_row.attempts,
                None if job_row is None else job_row.locked_until,
            )
            own.rollback()
            return None
        row = _lock_version(own, version)
        if row is None:
            own.rollback()
            return None
        if _row_says_skip(
            row,
            stage=stage,
            message=message,
            current=current,
            total=total,
            now=moment,
            force=force,
        ):
            _remember_stage(version, row)
            own.rollback()
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
        _remember_stage(version, row)
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
    """Stage the terminal write. The caller commits, so a lost lease can roll it back.

    Assigning ``job.status`` and then ``session.commit()`` would flush that
    status by primary key and overwrite the worker that reclaimed the row.
    """
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


def lease_requires_stage_reset(
    *,
    job_status: str | None,
    attempts: int,
    locked_until: datetime | None,
    now: datetime | None = None,
) -> bool:
    """True when no live worker holds the job and a stored stage would be stale.

    A running job with a missing or expired lease matches. A queued job that
    kept ``attempts >= 1`` matches. A first queue (``attempts == 0``) does not.
    A graceful SIGTERM that refunds the attempt also does not: that release
    calls ``reset_stage_on_requeue`` only when its own update rowcount is 1.
    """
    moment = now or datetime.now(UTC)
    if job_status == IngestJobStatus.RUNNING.value:
        if locked_until is None:
            return True
        return _as_utc(locked_until) < moment
    if job_status == IngestJobStatus.QUEUED.value:
        return attempts >= 1
    return False


def effective_ingest_stage(
    *,
    stage: str | None,
    stage_seq: int,
    stage_current: int | None,
    stage_total: int | None,
    stage_message: str | None,
    stage_updated_at: datetime | None,
    job_status: str | None,
    attempts: int,
    locked_until: datetime | None,
    now: datetime | None = None,
) -> EffectiveIngestStage:
    """Progress a reader should show. Does not write.

    An expired running lease or a kept-attempt requeue is shown as
    ``Restarting...`` so the status API and the stream cannot disagree.
    ``stage_updated_at`` stays the stored value so repeated reads keep one id.
    """
    stored = EffectiveIngestStage(
        stage=stage,
        stage_seq=stage_seq,
        stage_current=stage_current,
        stage_total=stage_total,
        stage_message=stage_message,
        stage_updated_at=stage_updated_at,
    )
    if not lease_requires_stage_reset(
        job_status=job_status,
        attempts=attempts,
        locked_until=locked_until,
        now=now,
    ):
        return stored
    if stage == _RESTART_STAGE and stage_message == RESTART_MESSAGE:
        return stored
    return EffectiveIngestStage(
        stage=_RESTART_STAGE,
        stage_seq=stage_seq + 1,
        stage_current=None,
        stage_total=None,
        stage_message=RESTART_MESSAGE,
        stage_updated_at=stage_updated_at,
    )


def ingest_lease_needs_stage_reset(job: IngestJob, *, now: datetime | None = None) -> bool:
    """True when ``job`` matches :func:`lease_requires_stage_reset`."""
    status = job.status.value if isinstance(job.status, IngestJobStatus) else str(job.status)
    return lease_requires_stage_reset(
        job_status=status,
        attempts=int(job.attempts or 0),
        locked_until=job.locked_until,
        now=now,
    )


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

"""Persist subject-feedback prose so a page load does not wait on OpenRouter.

The request records which facts hash it wants (`requested_hash`) and returns a
template immediately. A task scheduled after the response writes `summary` only
if that hash is still the one requested, so a newer attempt wins over a slow call.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from education_platform.db.session import get_session_factory
from education_platform.modules.assessments.models import SubjectFeedbackSummary

# Long enough for one completion, short enough that a dead task is retried on the next view.
_REFRESH_LEASE = timedelta(seconds=45)


@dataclass(frozen=True, slots=True)
class SummaryRefresh:
    student_id: UUID
    subject_id: UUID
    facts_hash: str
    prompt: str
    template_summary: str


def facts_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode()).hexdigest()


async def _row(
    session: AsyncSession, student_id: UUID, subject_id: UUID
) -> SubjectFeedbackSummary | None:
    return cast(
        SubjectFeedbackSummary | None,
        await session.scalar(
            select(SubjectFeedbackSummary).where(
                SubjectFeedbackSummary.student_id == student_id,
                SubjectFeedbackSummary.subject_id == subject_id,
            )
        ),
    )


async def cached_summary(
    session: AsyncSession, student_id: UUID, subject_id: UUID, digest: str
) -> str | None:
    row = await _row(session, student_id, subject_id)
    if row is None or row.facts_hash != digest or not row.summary:
        return None
    return row.summary


def _refresh_in_flight(row: SubjectFeedbackSummary, digest: str, now: datetime) -> bool:
    if row.requested_hash != digest or row.facts_hash == digest:
        return False
    updated_at = row.updated_at
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    return now - updated_at < _REFRESH_LEASE


async def claim_refresh(
    session: AsyncSession, student_id: UUID, subject_id: UUID, digest: str
) -> bool:
    """Reserve generation for this facts hash.

    Returns False when a fresh claim for the same hash is already in flight.
    Commits before returning: the refresh task runs before the request session
    closes, and it has to see this row.
    """
    row = await _row(session, student_id, subject_id)
    now = datetime.now(UTC)
    if row is not None and row.facts_hash == digest and row.summary:
        return False
    if row is not None and _refresh_in_flight(row, digest, now):
        return False

    stmt = pg_insert(SubjectFeedbackSummary).values(
        id=uuid4(),
        student_id=student_id,
        subject_id=subject_id,
        requested_hash=digest,
        facts_hash=None,
        summary=None,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_subject_feedback_summaries_student_subject",
        set_={"requested_hash": stmt.excluded.requested_hash, "updated_at": func.now()},
    )
    await session.execute(stmt)
    await session.commit()
    return True


async def store_refresh_result(job: SummaryRefresh, summary: str) -> None:
    """Write prose only when this hash is still the latest request."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        await session.execute(
            update(SubjectFeedbackSummary)
            .where(
                SubjectFeedbackSummary.student_id == job.student_id,
                SubjectFeedbackSummary.subject_id == job.subject_id,
                SubjectFeedbackSummary.requested_hash == job.facts_hash,
            )
            .values(summary=summary, facts_hash=job.facts_hash, updated_at=func.now())
        )
        await session.commit()

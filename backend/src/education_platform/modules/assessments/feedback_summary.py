"""Persist subject-feedback prose and queue the OpenRouter call.

The feedback request stores which facts hash it wants (`requested_hash`) and
returns a template immediately. A `feedback_summary_jobs` row is claimed by the
same worker that drains ingest and generation jobs, so the call survives an API
restart. The worker writes `summary` only when that hash is still the one
requested, so a newer attempt wins over a slow call.

`summary_status` on the response is:

- `ready` — stored prose came from the model for the current facts
- `pending` — a job is queued or running; the text is the template
- `fallback` — there is nothing to generate, or generation failed and the
  template was stored so the page does not retry on every view
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from sqlalchemy.sql import func, text

from education_platform.core.llm import OpenRouterError, chat_completion_json_sync
from education_platform.modules.assessments.models import (
    FeedbackSummaryJob,
    FeedbackSummaryJobStatus,
    FeedbackSummaryOrigin,
    SubjectFeedbackSummary,
)

SummaryStatus = Literal["ready", "pending", "fallback"]
_SUMMARY_TIMEOUT_SECONDS = 20.0

_IN_FLIGHT = (FeedbackSummaryJobStatus.QUEUED, FeedbackSummaryJobStatus.RUNNING)


@dataclass(frozen=True, slots=True)
class ResolvedSummary:
    text: str
    status: SummaryStatus


def facts_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode()).hexdigest()


def write_summary(prompt: str) -> str:
    """Ask OpenRouter for the prose. An empty string means use the template."""
    try:
        payload = chat_completion_json_sync(
            [
                {
                    "role": "system",
                    "content": (
                        "You write short, encouraging feedback summaries for a student, based "
                        "only on the facts given. Never invent claims not present in the facts. "
                        'Return JSON only, in this exact shape: {"summary": "..."}'
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            timeout=_SUMMARY_TIMEOUT_SECONDS,
        )
    except json.JSONDecodeError as exc:
        raise OpenRouterError("Feedback summary was not JSON") from exc
    summary = payload.get("summary")
    if not isinstance(summary, str):
        return ""
    return summary.strip()


def apply_summary(
    session: Session,
    job: FeedbackSummaryJob,
    summary: str,
    origin: FeedbackSummaryOrigin,
) -> None:
    """Write prose only when this job's hash is still the latest request."""
    session.execute(
        update(SubjectFeedbackSummary)
        .where(
            SubjectFeedbackSummary.student_id == job.student_id,
            SubjectFeedbackSummary.subject_id == job.subject_id,
            SubjectFeedbackSummary.requested_hash == job.facts_hash,
        )
        .values(
            summary=summary,
            facts_hash=job.facts_hash,
            summary_origin=origin,
            updated_at=func.now(),
        )
    )


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


async def _in_flight_job(
    session: AsyncSession, student_id: UUID, subject_id: UUID
) -> FeedbackSummaryJob | None:
    return cast(
        FeedbackSummaryJob | None,
        await session.scalar(
            select(FeedbackSummaryJob).where(
                FeedbackSummaryJob.student_id == student_id,
                FeedbackSummaryJob.subject_id == subject_id,
                FeedbackSummaryJob.status.in_(_IN_FLIGHT),
            )
        ),
    )


def _cached_status(row: SubjectFeedbackSummary, digest: str) -> ResolvedSummary | None:
    if row.facts_hash != digest or not row.summary or row.summary_origin is None:
        return None
    if row.summary_origin == FeedbackSummaryOrigin.MODEL:
        return ResolvedSummary(row.summary, "ready")
    if row.summary_origin == FeedbackSummaryOrigin.TEMPLATE:
        return ResolvedSummary(row.summary, "fallback")
    return None


async def resolve_summary(
    session: AsyncSession,
    student_id: UUID,
    subject_id: UUID,
    digest: str,
    prompt: str,
    template: str,
) -> ResolvedSummary:
    """Return cached prose, or enqueue one worker job and return the template.

    Commits when a job is queued. The request session commits only after the
    response is sent, and the worker has to see this row before that.
    """
    row = await _row(session, student_id, subject_id)
    if row is not None:
        cached = _cached_status(row, digest)
        if cached is not None:
            return cached

    await session.execute(
        pg_insert(SubjectFeedbackSummary)
        .values(
            id=uuid4(),
            student_id=student_id,
            subject_id=subject_id,
            requested_hash=digest,
        )
        .on_conflict_do_update(
            constraint="uq_subject_feedback_summaries_student_subject",
            set_={"requested_hash": digest, "updated_at": func.now()},
        )
    )
    await session.execute(
        pg_insert(FeedbackSummaryJob)
        .values(
            id=uuid4(),
            student_id=student_id,
            subject_id=subject_id,
            facts_hash=digest,
            prompt=prompt,
            template_summary=template,
            status=FeedbackSummaryJobStatus.QUEUED,
            attempts=0,
            max_attempts=3,
        )
        .on_conflict_do_nothing(
            index_elements=["student_id", "subject_id"],
            index_where=text("status IN ('queued', 'running')"),
        )
    )
    job = await _in_flight_job(session, student_id, subject_id)
    if (
        job is not None
        and job.status == FeedbackSummaryJobStatus.QUEUED
        and job.facts_hash != digest
    ):
        job.facts_hash = digest
        job.prompt = prompt
        job.template_summary = template
    await session.commit()
    return ResolvedSummary(template, "pending")

"""Business logic for self-evaluation recording and lookup."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from education_platform.modules.authorization.scope import Scope
from education_platform.modules.self_evaluation.models import SelfEvaluation
from education_platform.modules.self_evaluation.schemas import (
    SelfEvaluationIn,
    SelfEvaluationOut,
)


async def record_self_evaluation(
    session: AsyncSession,
    scope: Scope,
    subject_id: UUID,
    payload: SelfEvaluationIn,
) -> SelfEvaluationOut:
    """Insert one self-evaluation for the current student. Appends a new row each
    time (history), so calibration can be tracked over time; reads take the latest.
    """
    if scope.self_student_id is None:
        return SelfEvaluationOut(subject_id=subject_id, confidence=None)

    row = SelfEvaluation(
        student_id=scope.self_student_id,
        subject_id=subject_id,
        confidence=payload.confidence,
        recorded_at=datetime.now(UTC),
    )
    session.add(row)
    await session.commit()
    return SelfEvaluationOut(subject_id=subject_id, confidence=payload.confidence)


async def get_latest_self_evaluation(
    session: AsyncSession,
    scope: Scope,
    subject_id: UUID,
) -> SelfEvaluationOut:
    """Return the student's most recent confidence rating for a subject."""
    if scope.self_student_id is None:
        return SelfEvaluationOut(subject_id=subject_id, confidence=None)

    row = (
        await session.execute(
            select(SelfEvaluation.confidence)
            .where(
                SelfEvaluation.student_id == scope.self_student_id,
                SelfEvaluation.subject_id == subject_id,
            )
            .order_by(SelfEvaluation.recorded_at.desc())
            .limit(1)
        )
    ).first()

    return SelfEvaluationOut(subject_id=subject_id, confidence=row[0] if row is not None else None)

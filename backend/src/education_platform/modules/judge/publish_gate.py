"""Block publish while an enforced lesson still needs the existing QA accept."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from education_platform.modules.generation.models import ReviewRoundClosure, ReviewRoundRow
from education_platform.modules.generation.types import CloseAction, ReviewStage
from education_platform.modules.judge.models import JudgeEvaluation


async def lesson_publish_blocked(session: AsyncSession, run_id: UUID) -> bool:
    """True when the latest lesson judgment is an enforce hold and QA is open.

    Accepting the current QA revision (``accept_current``) at or after this
    hold clears it, without a second review system. Call this before publish
    seals the open round: that seal is also ``accept_current`` and is not the
    approval. An earlier accept of a previous revision does not clear a new hold.
    """
    latest = await session.scalar(
        select(JudgeEvaluation)
        .where(
            JudgeEvaluation.run_id == run_id,
            JudgeEvaluation.subject_kind == "lesson",
        )
        .order_by(JudgeEvaluation.created_at.desc())
        .limit(1)
    )
    if latest is None or latest.mode != "enforce" or latest.decision != "needs_review":
        return False
    accepted = await session.scalar(
        select(ReviewRoundClosure.id)
        .join(ReviewRoundRow, ReviewRoundClosure.round_id == ReviewRoundRow.id)
        .where(
            ReviewRoundRow.run_id == run_id,
            ReviewRoundRow.stage == ReviewStage.QA,
            ReviewRoundClosure.action == CloseAction.ACCEPT_CURRENT,
            ReviewRoundClosure.created_at >= latest.created_at,
        )
        .limit(1)
    )
    return accepted is None

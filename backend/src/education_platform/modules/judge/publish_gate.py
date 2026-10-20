"""Block publish while an enforced lesson still needs the existing QA accept."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from education_platform.modules.generation.models import ReviewRoundClosure, ReviewRoundRow
from education_platform.modules.generation.types import CloseAction, ReviewStage
from education_platform.modules.judge.models import JudgeEvaluation
from education_platform.modules.judge.policy import HOLD_CHECK_ID


def _is_hold(row: JudgeEvaluation) -> bool:
    if row.mode != "enforce" or row.decision != "needs_review":
        return False
    checks = row.checks if isinstance(row.checks, list) else []
    return any(isinstance(item, dict) and item.get("check_id") == HOLD_CHECK_ID for item in checks)


async def lesson_publish_blocked(session: AsyncSession, run_id: UUID) -> bool:
    """True when an enforce hold marker is still waiting for QA accept.

    Only a hold marker blocks publish. A borderline lesson evaluation does not.
    An error or unavailable row does not clear a hold that is already stored.
    Accepting the current QA revision (``accept_current``) at or after this
    hold clears it. Call this before publish seals the open round: that seal
    is also ``accept_current`` and is not the approval. An earlier accept of a
    previous revision does not clear a new hold.
    """
    rows = (
        await session.scalars(
            select(JudgeEvaluation)
            .where(
                JudgeEvaluation.run_id == run_id,
                JudgeEvaluation.subject_kind == "lesson",
            )
            .order_by(JudgeEvaluation.created_at.desc(), JudgeEvaluation.id.desc())
        )
    ).all()
    latest: JudgeEvaluation | None = None
    for row in rows:
        if row.decision == "error":
            continue
        latest = row
        break
    if latest is None or not _is_hold(latest):
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

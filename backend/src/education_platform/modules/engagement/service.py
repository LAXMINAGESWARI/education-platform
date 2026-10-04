"""Business logic for slide engagement recording and summary."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from education_platform.modules.authorization.scope import Scope
from education_platform.modules.engagement.models import SlideEngagement
from education_platform.modules.engagement.schemas import (
    SlideEngagementIn,
    SlideEngagementOut,
    SlideEngagementSummary,
    SubtopicEngagementSummary,
)


async def record_engagement(
    session: AsyncSession,
    scope: Scope,
    subtopic_id: UUID,
    payload: SlideEngagementIn,
) -> SlideEngagementOut:
    """Insert one slide engagement row for the current student.

    Uses scope.self_student_id (student_profiles.id) — consistent with at_risk_flags.
    Returns early if the caller is not a student (e.g. admin browsing lessons).
    """
    if scope.self_student_id is None:
        return SlideEngagementOut(recorded=False)

    row = SlideEngagement(
        student_id=scope.self_student_id,
        subtopic_id=subtopic_id,
        slide_index=payload.slide_index,
        seconds_active=payload.seconds_active,
        recorded_at=datetime.now(UTC),
    )
    session.add(row)
    await session.commit()
    return SlideEngagementOut(recorded=True)


async def get_subtopic_engagement_summary(
    session: AsyncSession,
    scope: Scope,
    subtopic_id: UUID,
) -> SubtopicEngagementSummary:
    """Return per-slide engagement totals for this student on a subtopic.

    Aggregates all recorded rows — each flush from the frontend is one row,
    so we sum seconds_active grouped by slide_index.
    """
    if scope.self_student_id is None:
        return SubtopicEngagementSummary(subtopic_id=subtopic_id, slides=[])

    rows = (
        await session.execute(
            select(
                SlideEngagement.slide_index,
                func.sum(SlideEngagement.seconds_active).label("total_seconds"),
            )
            .where(
                SlideEngagement.student_id == scope.self_student_id,
                SlideEngagement.subtopic_id == subtopic_id,
            )
            .group_by(SlideEngagement.slide_index)
            .order_by(SlideEngagement.slide_index)
        )
    ).all()

    return SubtopicEngagementSummary(
        subtopic_id=subtopic_id,
        slides=[
            SlideEngagementSummary(
                slide_index=row.slide_index,
                total_seconds=int(row.total_seconds),
            )
            for row in rows
        ],
    )

"""Business logic for slide engagement recording and summary."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from education_platform.modules.authorization.scope import Scope
from education_platform.modules.engagement.models import SlideEngagement
from education_platform.modules.engagement.schemas import (
    EngagementRateOut,
    SlideEngagementIn,
    SlideEngagementOut,
    SlideEngagementSummary,
    SubtopicEngagementSummary,
)
from education_platform.modules.materials import service as materials_service

# A slide counts as "engaged" if the student spent at least this many seconds on it.
# Starting value (option A); task 2 layers a class-relative threshold on top.
ENGAGED_THRESHOLD_SECONDS = 5


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
    """Return per-slide engagement totals for this student on a subtopic."""
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


async def get_subtopic_engagement_rate(
    session: AsyncSession,
    scope: Scope,
    subtopic_id: UUID,
) -> EngagementRateOut:
    """Compute engagement rate for this student on a subtopic.

    engaged_slides = distinct slides where total active time >= threshold
    total_slides   = number of slides in the published lesson
    rate           = engaged_slides / total_slides  (None if no slides)
    """
    empty = EngagementRateOut(
        subtopic_id=subtopic_id, engaged_slides=0, total_slides=0, engagement_rate=None
    )
    if scope.self_student_id is None:
        return empty

    # Denominator: total slides in the lesson (reuse the materials service).
    lesson = await materials_service.get_subtopic_lesson(session, scope, subtopic_id)
    total_slides = len(lesson.slides)
    if total_slides == 0:
        return empty

    # Numerator: distinct slides the student spent >= threshold seconds on.
    engaged_rows = (
        await session.execute(
            select(SlideEngagement.slide_index)
            .where(
                SlideEngagement.student_id == scope.self_student_id,
                SlideEngagement.subtopic_id == subtopic_id,
            )
            .group_by(SlideEngagement.slide_index)
            .having(func.sum(SlideEngagement.seconds_active) >= ENGAGED_THRESHOLD_SECONDS)
        )
    ).all()
    engaged_slides = len(engaged_rows)

    return EngagementRateOut(
        subtopic_id=subtopic_id,
        engaged_slides=engaged_slides,
        total_slides=total_slides,
        engagement_rate=round(engaged_slides / total_slides, 3),
    )

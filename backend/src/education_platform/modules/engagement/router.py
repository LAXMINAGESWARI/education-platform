"""Slide engagement API endpoints.

POST /api/v1/me/lessons/{subtopic_id}/engagement
  Records active seconds a student spent on a single lesson slide.
  Called by LessonSlidesPage.tsx on slide change and unmount.

GET  /api/v1/me/lessons/{subtopic_id}/engagement
  Returns per-slide active time totals for the current student.
  Used by the student dashboard to surface engagement signals.
"""

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from education_platform.api.deps import get_scope
from education_platform.db.session import get_session
from education_platform.modules.authorization.scope import Scope
from education_platform.modules.engagement import service
from education_platform.modules.engagement.schemas import (
    SlideEngagementIn,
    SlideEngagementOut,
    SubtopicEngagementSummary,
)

router = APIRouter(tags=["engagement"])


@router.post(
    "/me/lessons/{subtopic_id}/engagement",
    response_model=SlideEngagementOut,
    summary="Record slide engagement",
)
async def post_slide_engagement(
    subtopic_id: UUID,
    payload: SlideEngagementIn,
    scope: Scope = Depends(get_scope),
    session: AsyncSession = Depends(get_session),
) -> SlideEngagementOut:
    """Fire-and-forget from the frontend — always returns 200."""
    return await service.record_engagement(session, scope, subtopic_id, payload)


@router.get(
    "/me/lessons/{subtopic_id}/engagement",
    response_model=SubtopicEngagementSummary,
    summary="Get slide engagement summary",
)
async def get_slide_engagement(
    subtopic_id: UUID,
    scope: Scope = Depends(get_scope),
    session: AsyncSession = Depends(get_session),
) -> SubtopicEngagementSummary:
    """Returns per-slide active time totals for the student dashboard."""
    return await service.get_subtopic_engagement_summary(session, scope, subtopic_id)

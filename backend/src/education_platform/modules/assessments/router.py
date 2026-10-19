"""Assessment / quiz attempt routes."""

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from education_platform.api.deps import get_scope
from education_platform.db.session import get_session
from education_platform.modules.assessments import (
    feedback_hierarchy,
    feedback_service,
    goals_service,
    service,
)
from education_platform.modules.assessments.schemas import (
    AttemptHistoryItem,
    AttemptResult,
    FeedbackHighlights,
    FeedbackOverview,
    FeedbackSubjectDetail,
    FeedbackTopicDetail,
    GoalOut,
    SetGoalRequest,
    StartAttemptResponse,
    SubjectFeedbackDashboard,
    SubmitAttemptRequest,
)
from education_platform.modules.authorization.scope import Scope

router = APIRouter(tags=["attempts"])


@router.post("/quizzes/{quiz_id}/attempts", response_model=StartAttemptResponse)
async def start_quiz_attempt(
    quiz_id: UUID,
    scope: Scope = Depends(get_scope),
    session: AsyncSession = Depends(get_session),
) -> StartAttemptResponse:
    return await service.start_attempt(session, scope, quiz_id)


@router.get("/quizzes/{quiz_id}/attempts", response_model=list[AttemptHistoryItem])
async def list_quiz_attempts(
    quiz_id: UUID,
    scope: Scope = Depends(get_scope),
    session: AsyncSession = Depends(get_session),
) -> list[AttemptHistoryItem]:
    return await service.list_attempts_for_quiz(session, scope, quiz_id)


@router.post("/attempts/{attempt_id}/submit", response_model=AttemptResult)
async def submit_quiz_attempt(
    attempt_id: UUID,
    payload: SubmitAttemptRequest,
    scope: Scope = Depends(get_scope),
    session: AsyncSession = Depends(get_session),
) -> AttemptResult:
    return await service.submit_attempt(session, scope, attempt_id, payload)


@router.get("/attempts/{attempt_id}", response_model=AttemptResult)
async def get_quiz_attempt(
    attempt_id: UUID,
    scope: Scope = Depends(get_scope),
    session: AsyncSession = Depends(get_session),
) -> AttemptResult:
    return await service.get_attempt(session, scope, attempt_id)


@router.get("/subjects/{subject_id}/feedback", response_model=SubjectFeedbackDashboard)
async def get_subject_feedback(
    subject_id: UUID,
    scope: Scope = Depends(get_scope),
    session: AsyncSession = Depends(get_session),
) -> SubjectFeedbackDashboard:
    return await feedback_service.get_subject_feedback(session, scope, subject_id)


@router.get("/me/feedback", response_model=FeedbackOverview)
async def get_feedback_overview(
    scope: Scope = Depends(get_scope),
    session: AsyncSession = Depends(get_session),
) -> FeedbackOverview:
    return await feedback_hierarchy.get_overview(session, scope)


@router.get("/me/feedback/subjects/{subject_id}", response_model=FeedbackSubjectDetail)
async def get_feedback_subject(
    subject_id: UUID,
    scope: Scope = Depends(get_scope),
    session: AsyncSession = Depends(get_session),
) -> FeedbackSubjectDetail:
    return await feedback_hierarchy.get_subject(session, scope, subject_id)


@router.get("/me/feedback/topics/{topic_id}", response_model=FeedbackTopicDetail)
async def get_feedback_topic(
    topic_id: UUID,
    scope: Scope = Depends(get_scope),
    session: AsyncSession = Depends(get_session),
) -> FeedbackTopicDetail:
    return await feedback_hierarchy.get_topic(session, scope, topic_id)


@router.get("/me/feedback-highlights", response_model=FeedbackHighlights)
async def get_feedback_highlights(
    scope: Scope = Depends(get_scope),
    session: AsyncSession = Depends(get_session),
) -> FeedbackHighlights:
    return await feedback_service.get_feedback_highlights(session, scope)


@router.put("/subtopics/{subtopic_id}/goal", response_model=GoalOut)
async def put_subtopic_goal(
    subtopic_id: UUID,
    payload: SetGoalRequest,
    scope: Scope = Depends(get_scope),
    session: AsyncSession = Depends(get_session),
) -> GoalOut:
    return await goals_service.set_goal(session, scope, subtopic_id, payload)


@router.delete("/subtopics/{subtopic_id}/goal", status_code=status.HTTP_204_NO_CONTENT)
async def delete_subtopic_goal(
    subtopic_id: UUID,
    scope: Scope = Depends(get_scope),
    session: AsyncSession = Depends(get_session),
) -> None:
    await goals_service.delete_goal(session, scope, subtopic_id)
